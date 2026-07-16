# SPDX-License-Identifier: AGPL-3.0-or-later
"""Table `packet_counts` + `node_names` : écriture, agrégat SQL du contrat A, plafond dur."""
import sqlite3

from mbg.storage import PACKET_RETENTION_SECONDS, MetricsStore


class Clock:
    """Horloge pilotable (le re-binning et la purge se jugent sur des `ts` exacts)."""

    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t


def test_record_packets_is_a_time_series_never_an_upsert(tmp_path):
    clock = Clock(1000.0)
    store = MetricsStore(str(tmp_path / "m.db"), clock=clock)
    store.record_packets({"!a": 3})
    clock.t = 2000.0
    store.record_packets({"!a": 5})  # même node, plus tard : une 2e LIGNE, pas un écrasement
    with store._conn() as conn:
        rows = [tuple(r) for r in conn.execute("SELECT ts,node_id,count FROM packet_counts ORDER BY ts")]
    assert rows == [(1000.0, "!a", 3), (2000.0, "!a", 5)]


def test_record_packets_empty_writes_nothing(tmp_path):
    store = MetricsStore(str(tmp_path / "m.db"))
    store.record_packets({})
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) AS c FROM packet_counts").fetchone()["c"] == 0


def test_packet_history_bins_and_sums_in_sql(tmp_path):
    clock = Clock()
    store = MetricsStore(str(tmp_path / "m.db"), clock=clock)
    # Deux flushes DANS la même tranche de 900 s -> une seule ligne, sommée.
    for ts, counts in ((1783622100.0, {"!a": 40, "!b": 7}), (1783622400.0, {"!a": 2})):
        clock.t = ts
        store.record_packets(counts)
    clock.t = 1783623100.0  # tranche suivante
    store.record_packets({"!a": 1})
    out = store.packet_history(since=0, bin_seconds=900)
    assert out["bin"] == 900  # bin réfléchi dans la réponse
    # bin_start = floor(ts/900)*900 ; triées par bin_start croissant.
    assert out["rows"] == [
        [1783621800, "!a", 42], [1783621800, "!b", 7], [1783622700, "!a", 1],
    ]
    assert [r[0] for r in out["rows"]] == sorted(r[0] for r in out["rows"])


def test_packet_history_since_filters(tmp_path):
    clock = Clock()
    store = MetricsStore(str(tmp_path / "m.db"), clock=clock)
    clock.t = 1000.0
    store.record_packets({"!vieux": 9})
    clock.t = 5000.0
    store.record_packets({"!recent": 4})
    out = store.packet_history(since=2000.0, bin_seconds=60)
    assert out["rows"] == [[4980, "!recent", 4]]
    assert out["nodes"] == {"!recent": "!recent"}  # `nodes` ne liste QUE les nodes de rows


def test_packet_history_resolves_names_short_then_long_then_id(tmp_path):
    store = MetricsStore(str(tmp_path / "m.db"), clock=Clock(1000.0))
    store.upsert_node_names([
        {"node_id": "!court", "short_name": "PM", "long_name": "Piton Maïdo"},  # shortName gagne
        {"node_id": "!long", "short_name": None, "long_name": "Relais Tampon"},  # repli longName
        {"node_id": "!vide", "short_name": "", "long_name": ""},                 # repli node_id
        {"node_id": "!absent-de-rows", "short_name": "Fantôme", "long_name": "F"},
    ])
    store.record_packets({"!court": 1, "!long": 1, "!vide": 1, "!inconnu": 1})
    nodes = store.packet_history(since=0, bin_seconds=300)["nodes"]
    assert nodes == {
        "!court": "PM", "!long": "Relais Tampon",
        "!vide": "!vide",        # nommé mais vide -> repli
        "!inconnu": "!inconnu",  # jamais vu par la sonde -> repli, JAMAIS absent ni null
    }
    assert "!absent-de-rows" not in nodes  # `nodes` se limite aux nodes présents dans rows


def test_packet_history_empty_db(tmp_path):
    store = MetricsStore(str(tmp_path / "m.db"))
    assert store.packet_history(since=0, bin_seconds=900) == {"bin": 900, "nodes": {}, "rows": []}


def test_upsert_node_names_updates_and_ignores_empty(tmp_path):
    clock = Clock(100.0)
    store = MetricsStore(str(tmp_path / "m.db"), clock=clock)
    store.upsert_node_names([{"node_id": "!a", "short_name": "vieux", "long_name": "V"}])
    clock.t = 200.0
    store.upsert_node_names([{"node_id": "!a", "short_name": "neuf", "long_name": "N"}])
    store.upsert_node_names([])  # lot vide : aucune écriture
    with store._conn() as conn:
        rows = [tuple(r) for r in conn.execute("SELECT node_id,short_name,updated FROM node_names")]
    assert rows == [("!a", "neuf", 200.0)]  # une ligne par node (PK), mise à jour en place


def test_prune_packets_caps_only_packet_counts(tmp_path):
    clock = Clock(0.0)
    store = MetricsStore(str(tmp_path / "m.db"), clock=clock)
    store.record_packets({"!vieux": 1})
    store.record_node({"battery_level": 80})
    store.record_link(1)
    store.upsert_node_names([{"node_id": "!vieux", "short_name": "V", "long_name": "V"}])
    clock.t = 40 * 86400  # 40 j plus tard
    store.record_packets({"!neuf": 2})
    store.prune_packets(PACKET_RETENTION_SECONDS)
    with store._conn() as conn:
        packets = [r["node_id"] for r in conn.execute("SELECT node_id FROM packet_counts")]
        others = [
            conn.execute("SELECT count(*) AS c FROM node_metrics").fetchone()["c"],
            conn.execute("SELECT count(*) AS c FROM link_quality").fetchone()["c"],
            conn.execute("SELECT count(*) AS c FROM node_names").fetchone()["c"],
        ]
    assert packets == ["!neuf"]  # au-delà de 35 j : purgé
    # Les tables gouvernées par retention_days (0 par défaut = tout garder) sont INTACTES, et
    # node_names survit pour pouvoir nommer les comptages encore en fenêtre.
    assert others == [1, 1, 1]


def test_prune_also_purges_packets_when_retention_is_set(tmp_path):
    # packet_counts est dans _TS_TABLES : une station qui fixe retention_days EN DEÇÀ de 35 j
    # la purge plus tôt. 35 j est un plafond, pas un plancher.
    clock = Clock(0.0)
    store = MetricsStore(str(tmp_path / "m.db"), clock=clock)
    store.record_packets({"!a": 1})
    clock.t = 10 * 86400
    store.prune(2 * 86400)
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) AS c FROM packet_counts").fetchone()["c"] == 0


def test_export_csv_includes_packets_and_names(tmp_path):
    store = MetricsStore(str(tmp_path / "m.db"), clock=Clock(1000.0))
    store.record_packets({"!a": 3})
    store.upsert_node_names([{"node_id": "!a", "short_name": "A", "long_name": "Node A"}])
    out = tmp_path / "csv"
    store.export_csv(str(out))
    assert "!a" in (out / "packet_counts.csv").read_text()
    assert "Node A" in (out / "node_names.csv").read_text()


def test_new_tables_are_created_on_a_preexisting_db(tmp_path):
    # Une table NEUVE n'a pas besoin d'entrée _MIGRATIONS : CREATE TABLE IF NOT EXISTS la crée
    # sur une base déjà en prod. Régression de la classe de bug du crash-loop v0.9.0.
    path = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE node_metrics (ts REAL, battery_level INTEGER)")
    conn.commit()
    conn.close()
    store = MetricsStore(path, clock=Clock(1783622100.0))
    store.record_packets({"!a": 1})  # le chemin qui crasherait si la table manquait
    store.upsert_node_names([{"node_id": "!a", "short_name": "A", "long_name": "A"}])
    assert store.packet_history(since=0) == {
        "bin": 300, "nodes": {"!a": "A"}, "rows": [[1783622100, "!a", 1]],
    }
