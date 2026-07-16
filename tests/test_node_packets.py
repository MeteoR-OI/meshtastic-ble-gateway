# SPDX-License-Identifier: AGPL-3.0-or-later
"""Comptage des paquets entrants par nœud (chemin radio) + drain vers le flush."""
import threading

from fakes import FakeIface
from mbg.node import MeshtasticNodeLink


def _link(**kwargs):
    return MeshtasticNodeLink(
        "addr", lambda m: None, interface_factory=FakeIface,
        subscribe=lambda h, t: None, unsubscribe=lambda h, t: None, **kwargs,
    )


def test_counts_every_portnum_not_just_routing():
    # Le contrat A compte TOUS portnums confondus : le comptage est en amont du filtre portnum
    # (qui, lui, ne laisse passer que ROUTING_APP vers le suivi d'ACK).
    link = _link()
    link._handler_receive(packet={"fromId": "!a", "decoded": {"portnum": "TEXT_MESSAGE_APP"}})
    link._handler_receive(packet={"fromId": "!a", "decoded": {"portnum": "POSITION_APP"}})
    link._handler_receive(packet={"fromId": "!b", "decoded": {"portnum": "ROUTING_APP"}})
    link._handler_receive(packet={"fromId": "!c"})  # pas de bloc `decoded` du tout
    assert link.drain_packet_counts() == {"!a": 2, "!b": 1, "!c": 1}


def test_falls_back_to_numeric_from_field():
    # Dict DÉJÀ décodé (≠ ServiceEnvelope protobuf de proxy.py) : `fromId` d'abord, repli sur le
    # `from` numérique, formaté comme metrics.neighbors().
    link = _link()
    link._handler_receive(packet={"from": 0xA4F2C1B0})
    link._handler_receive(packet={"from": 0xA4F2C1B0, "fromId": None})
    assert link.drain_packet_counts() == {"!a4f2c1b0": 2}


def test_never_raises_and_never_invents_a_node():
    # Chemin radio : une exception ici casserait la réception. Un paquet inexploitable est
    # ignoré SANS être compté : l'attribuer à un "!00000000" de repli créerait un nœud fantôme
    # dans l'histogramme et dans la légende du chart.
    link = _link()
    link._handler_receive(packet=None)  # meshtastic peut appeler sans paquet
    link._handler_receive(packet={"from": "pas-un-entier"})  # `& 0xFFFFFFFF` sur une str
    link._handler_receive(packet={})  # ni fromId ni from
    link._handler_receive(packet={"fromId": "", "from": None})  # émetteur vide
    assert link.drain_packet_counts() == {}


def test_counts_the_local_node_too():
    """DÉCISION PRODUIT (arbitrage 2026-07-16, validée banc PAM289) : le nœud LOCAL compte.

    « Il émet, donc il compte. » Asymétrie ASSUMÉE avec `metrics.neighbors()`, qui exclut
    `my_num` : l'histogramme montre N+1 émetteurs là où le voisinage compte N voisins. Ce test
    existe pour qu'un futur lecteur ne « corrige » pas cette différence en croyant à un bug —
    la faire échouer, c'est casser le contrat, pas réparer quoi que ce soit.
    """
    link = _link()
    link._iface = type("I", (), {"getMyNodeInfo": lambda self: {"num": 0x534BBEA5}})()
    link._handler_receive(packet={"fromId": "!534bbea5"})  # la passerelle elle-même (ACK broadcast)
    link._handler_receive(packet={"fromId": "!aaaa0001"})  # un voisin
    assert link.drain_packet_counts() == {"!534bbea5": 1, "!aaaa0001": 1}


def test_drain_empties_the_counter():
    link = _link()
    link._handler_receive(packet={"fromId": "!a"})
    assert link.drain_packet_counts() == {"!a": 1}
    assert link.drain_packet_counts() == {}  # vidé : pas de double comptage au flush suivant
    link._handler_receive(packet={"fromId": "!a"})
    assert link.drain_packet_counts() == {"!a": 1}  # et il repart de zéro


def test_counting_is_thread_safe():
    # Le handler tourne dans le thread pubsub, le drain dans la boucle worker : sans verrou, un
    # compte se perdrait. On vérifie qu'aucun paquet n'est perdu sous concurrence réelle.
    link = _link()
    start = threading.Event()

    def emit():
        start.wait()
        for _ in range(500):
            link._handler_receive(packet={"fromId": "!a"})

    threads = [threading.Thread(target=emit) for _ in range(4)]
    for t in threads:
        t.start()
    start.set()
    for t in threads:
        t.join()
    assert link.drain_packet_counts() == {"!a": 2000}


def test_read_metrics_exposes_node_names():
    link = _link()
    link._iface = type(
        "I", (), {
            "getMyNodeInfo": lambda self: {"num": 1, "user": {"id": "!01", "longName": "Moi"}},
            "nodesByNum": {
                1: {"user": {"id": "!01", "shortName": "Moi", "longName": "Moi"}, "hopsAway": 0},
                2: {"user": {"id": "!02", "shortName": "V", "longName": "Voisin"}, "hopsAway": 0,
                    "lastHeard": 500},
            },
            "localNode": None,
        },
    )()
    data = link.read_metrics()
    # Le node LOCAL est présent dans node_names (il émet des ROUTING_APP d'ACK broadcast, donc
    # il peut apparaître dans rows) alors que `neighbors` l'exclut.
    assert data["node_names"] == [
        {"node_id": "!01", "short_name": "Moi", "long_name": "Moi"},
        {"node_id": "!02", "short_name": "V", "long_name": "Voisin"},
    ]
    assert [n["node_id"] for n in data["neighbors"]] == ["!02"]
