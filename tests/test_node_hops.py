# SPDX-License-Identifier: AGPL-3.0-or-later
"""Comptage des paquets entrants par nombre de sauts (chemin radio) + drain vers le flush.

Formule gelée (contrat A, amendée 2026-07-17) : `hopStart`/`hopLimit` top-level camelCase, un
`hopLimit` omis vaut 0 (MessageToDict omet les zéros → budget de sauts épuisé, PAS Inconnu). Seul
`hopStart` absent/0/non-int rend le saut indéterminable → bucket `-1`.
"""
import threading

import pytest

from fakes import FakeIface
from mbg.node import MeshtasticNodeLink, _hop_bucket


def _link(**kwargs):
    return MeshtasticNodeLink(
        "addr", lambda m: None, interface_factory=FakeIface,
        subscribe=lambda h, t: None, unsubscribe=lambda h, t: None, **kwargs,
    )


@pytest.mark.parametrize(
    "packet, expected",
    [
        # hopStart & hopLimit présents : sauts = hopStart - hopLimit.
        ({"hopStart": 3, "hopLimit": 1}, 2),
        ({"hopStart": 3, "hopLimit": 3}, 0),   # reçu intact -> Direct/0-hop
        ({"hopStart": 7, "hopLimit": 0}, 7),   # hopLimit=0 explicite -> budget épuisé, 7 sauts
        # hopLimit OMIS (MessageToDict omet les zéros) : vaut 0, PAS Inconnu -> hops = hopStart.
        ({"hopStart": 3}, 3),
        ({"hopStart": 1}, 1),
        # hopStart absent/0/non-int -> Inconnu (-1), seul cas de repli après l'amendement.
        ({"hopLimit": 2}, -1),                 # hopStart absent
        ({}, -1),                              # ni l'un ni l'autre
        ({"hopStart": 0, "hopLimit": 0}, -1),  # hopStart=0 (firmware ancien ne le peuple pas)
        ({"hopStart": "3", "hopLimit": 1}, -1),  # hopStart non-int
        # Incohérences -> -1 : hs < hl (négatif), hors [0..7].
        ({"hopStart": 1, "hopLimit": 3}, -1),  # négatif
        ({"hopStart": 10, "hopLimit": 0}, -1),  # 10 hors [0..7]
        ({"hopStart": 8}, -1),                 # 8 hors [0..7] (hopLimit omis = 0)
        # bool est sous-classe d'int : hopStart bool -> non exploitable (-1) ; hopLimit bool -> 0.
        ({"hopStart": True, "hopLimit": 0}, -1),
        ({"hopStart": 3, "hopLimit": True}, 3),  # hopLimit bool ignoré -> 0 -> hops=3
    ],
)
def test_hop_bucket_formula(packet, expected):
    assert _hop_bucket(packet) == expected


def test_counts_by_hop_same_population_as_packets():
    # Le saut se compte sur la MÊME population que /packets (invariant Σ count(hops) == total).
    link = _link()
    link._handler_receive(packet={"fromId": "!a", "hopStart": 3, "hopLimit": 1})  # 2 sauts
    link._handler_receive(packet={"fromId": "!b", "hopStart": 3, "hopLimit": 3})  # Direct
    link._handler_receive(packet={"fromId": "!c", "hopStart": 3})                 # 3 (hopLimit omis)
    link._handler_receive(packet={"fromId": "!d"})                                # -1 (pas de hop)
    assert link.drain_packet_hop_counts() == {2: 1, 0: 1, 3: 1, -1: 1}


def test_unexploitable_emitter_is_not_hop_counted():
    # Un paquet sans émetteur attribuable n'est compté NI par nœud NI par saut : l'invariant
    # Σ count(hops) == total /packets doit tenir (même population, même court-circuit).
    link = _link()
    link._handler_receive(packet={"hopStart": 3, "hopLimit": 1})  # pas de fromId/from -> ignoré
    link._handler_receive(packet={"from": None, "hopStart": 2})   # émetteur vide -> ignoré
    assert link.drain_packet_hop_counts() == {}
    assert link.drain_packet_counts() == {}


def test_never_raises_on_malformed_packet():
    # Chemin radio : une exception casserait la réception. Aucun paquet exotique ne doit lever.
    link = _link()
    link._handler_receive(packet=None)  # meshtastic peut appeler sans paquet
    link._handler_receive(packet={"from": "pas-un-entier", "hopStart": 3})  # from non-int
    link._handler_receive(packet={"fromId": "!x", "hopStart": [1, 2]})  # hopStart exotique -> -1
    # Seul le dernier a un émetteur exploitable -> un seul comptage, en -1.
    assert link.drain_packet_hop_counts() == {-1: 1}


def test_drain_empties_the_hop_counter():
    link = _link()
    link._handler_receive(packet={"fromId": "!a", "hopStart": 3, "hopLimit": 2})
    assert link.drain_packet_hop_counts() == {1: 1}
    assert link.drain_packet_hop_counts() == {}  # vidé : pas de double comptage au flush suivant
    link._handler_receive(packet={"fromId": "!a", "hopStart": 3, "hopLimit": 2})
    assert link.drain_packet_hop_counts() == {1: 1}  # repart de zéro


def test_hop_counting_is_thread_safe():
    # Le handler tourne dans le thread pubsub, le drain dans la boucle worker : sans verrou un
    # compte se perdrait. Aucun paquet ne doit être perdu sous concurrence réelle.
    link = _link()
    start = threading.Event()

    def emit():
        start.wait()
        for _ in range(500):
            link._handler_receive(packet={"fromId": "!a", "hopStart": 3, "hopLimit": 1})

    threads = [threading.Thread(target=emit) for _ in range(4)]
    for t in threads:
        t.start()
    start.set()
    for t in threads:
        t.join()
    assert link.drain_packet_hop_counts() == {2: 2000}
