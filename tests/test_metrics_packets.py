# SPDX-License-Identifier: AGPL-3.0-or-later
"""Noms affichables de la NodeDB (bloc `nodes` du contrat /packets)."""
from mbg.metrics import neighbors, node_names


def test_node_names_reads_short_and_long():
    out = node_names({0x1234: {"user": {"id": "!00001234", "shortName": "PM", "longName": "Piton Maïdo"}}})
    assert out == [{"node_id": "!00001234", "short_name": "PM", "long_name": "Piton Maïdo"}]


def test_node_names_falls_back_to_hex_id():
    # `user` sans `id` -> l'id est dérivé du num (même règle que neighbors()).
    out = node_names({0xA4F2C1B0: {"user": {"shortName": "X"}}})
    assert out[0]["node_id"] == "!a4f2c1b0"
    assert out[0]["long_name"] is None


def test_node_names_skips_unnamed_and_handles_empty():
    # Aucun nom exploitable -> omis (le repli node_id se fait à la lecture, cf. packet_history).
    assert node_names({1: {"user": {"id": "!01"}}, 2: {}, 3: {"user": {"shortName": ""}}}) == []
    assert node_names({}) == []
    assert node_names(None) == []


def test_node_names_is_a_superset_of_neighbors():
    """Le point qui justifie une table dédiée plutôt qu'un JOIN sur neighbor_registry.

    `neighbors()` jette le node à `hopsAway` inconnu, l'inactif, et le node local — or tous les
    trois PEUVENT émettre des paquets, donc apparaître dans `rows` et avoir besoin d'un nom.
    """
    db = {
        1: {"user": {"id": "!01", "shortName": "moi"}, "hopsAway": 0, "lastHeard": 1000},   # node local
        2: {"user": {"id": "!02", "shortName": "sans-hops"}, "lastHeard": 1000},            # hopsAway inconnu
        3: {"user": {"id": "!03", "shortName": "perime"}, "hopsAway": 0, "lastHeard": 1},   # inactif
        4: {"user": {"id": "!04", "shortName": "actif"}, "hopsAway": 0, "lastHeard": 1000},
    }
    actifs = {n["node_id"] for n in neighbors(db, my_num=1, now=1000.0, active_window=100.0)}
    nommes = {n["node_id"] for n in node_names(db)}
    assert actifs == {"!04"}  # neighbors() ne retient que le voisin actif à hops connu
    assert nommes == {"!01", "!02", "!03", "!04"}  # node_names nomme TOUT ce qui peut être compté
    assert actifs < nommes  # strictement : un JOIN sur le registre perdrait 3 noms sur 4
