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


def _link_with_local(num):
    """Link dont l'iface expose un nœud local `num` (source du bucket -2, cf. _local_node_id)."""
    link = _link()
    link._iface = type("I", (), {"getMyNodeInfo": lambda self: {"num": num}})()
    return link


def test_local_node_goes_to_bucket_minus_2_priority_over_hopstart():
    """Finding live PAM289 : le nœud LOCAL émet ~92 % de ses paquets SANS hopStart (peuplé
    seulement à la transmission mesh). Il doit aller dans un bucket DÉDIÉ -2, sinon il noie
    l'Inconnu -1 (qui veut dire « paquet DISTANT au saut indéterminé »). -2 est PRIORITAIRE sur
    le calcul du saut : l'émetteur tranche, pas le champ `hopStart`.
    """
    link = _link_with_local(0x534BBEA5)
    link._handler_receive(packet={"fromId": "!534bbea5"})  # local SANS hopStart -> -2 (pas -1)
    link._handler_receive(packet={"fromId": "!534bbea5", "hopStart": 3, "hopLimit": 1})  # local AVEC
    link._handler_receive(packet={"from": 0x534BBEA5, "hopStart": 3})  # local via `from` num -> -2
    link._handler_receive(packet={"fromId": "!aaaa0001", "hopStart": 3, "hopLimit": 1})  # distant -> 2
    link._handler_receive(packet={"fromId": "!aaaa0002"})  # distant sans hopStart -> -1
    assert link.drain_packet_hop_counts() == {-2: 3, 2: 1, -1: 1}


def test_invariant_sum_hops_equals_total_packets_with_local_bucket():
    # Σ count(hops, TOUS buckets dont -1 et -2) == total /packets : le local reste compté, juste
    # dans son bucket. Chaque paquet attribuable incrémente exactement un bucket de chaque compteur.
    link = _link_with_local(0x534BBEA5)
    packets = [
        {"fromId": "!534bbea5"},                               # local -2
        {"fromId": "!534bbea5", "hopStart": 4, "hopLimit": 4},  # local -2 (malgré Direct apparent)
        {"fromId": "!aaaa0001", "hopStart": 3, "hopLimit": 1},  # distant 2
        {"fromId": "!aaaa0002"},                               # distant -1
        {"from": 0xAAAA0003, "hopStart": 3},                   # distant 3
    ]
    for p in packets:
        link._handler_receive(packet=p)
    hop_total = sum(link.drain_packet_hop_counts().values())
    pkt_total = sum(link.drain_packet_counts().values())
    assert hop_total == pkt_total == len(packets)


def test_local_id_unknown_falls_back_to_normal_calc():
    # Tant que l'iface ne connaît pas le num local (None), aucun paquet ne matche -2 : le calcul
    # normal s'applique (comportement d'avant l'ajout du bucket). Pas d'iface -> _local_node_id None.
    link = _link()  # pas d'iface -> self._iface is None
    link._handler_receive(packet={"fromId": "!534bbea5", "hopStart": 3, "hopLimit": 1})  # -> 2, pas -2
    assert link.drain_packet_hop_counts() == {2: 1}


def test_local_id_is_resolved_once_and_survives_bad_getmynodeinfo():
    # _local_node_id ne lève jamais (getMyNodeInfo exotique) et mémoïse dès qu'un num est connu.
    link = _link()
    link._iface = type("I", (), {"getMyNodeInfo": lambda self: (_ for _ in ()).throw(RuntimeError)})()
    link._handler_receive(packet={"fromId": "!aaaa0001"})  # getMyNodeInfo lève -> local None -> -1
    assert link.drain_packet_hop_counts() == {-1: 1}
    # num connu ensuite -> résolu et mémoïsé
    link._iface = type("I", (), {"getMyNodeInfo": lambda self: {"num": 0x534BBEA5}})()
    link._handler_receive(packet={"fromId": "!534bbea5"})
    assert link._local_id == "!534bbea5"
    assert link.drain_packet_hop_counts() == {-2: 1}


def test_getmynodeinfo_without_num_leaves_local_unresolved():
    # getMyNodeInfo sans clé `num` -> local reste None -> pas de -2.
    link = _link()
    link._iface = type("I", (), {"getMyNodeInfo": lambda self: {}})()
    link._handler_receive(packet={"fromId": "!534bbea5"})  # non reconnu local -> -1 (sans hopStart)
    assert link.drain_packet_hop_counts() == {-1: 1}
    assert link._local_id is None


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
