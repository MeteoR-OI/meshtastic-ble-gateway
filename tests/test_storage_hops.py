# SPDX-License-Identifier: AGPL-3.0-or-later
"""Table `packet_hops` : écriture, agrégat SQL du contrat A (/hops), plafond dur partagé."""
import sqlite3

from mbg.storage import PACKET_RETENTION_SECONDS, MetricsStore


class Clock:
    """Horloge pilotable (le re-binning et la purge se jugent sur des `ts` exacts)."""

    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t


def test_record_packet_hops_is_a_time_series_never_an_upsert(tmp_path):
    clock = Clock(1000.0)
    store = MetricsStore(str(tmp_path / "m.db"), clock=clock)
    store.record_packet_hops({0: 3})
    clock.t = 2000.0
    store.record_packet_hops({0: 5})  # même bucket, plus tard : une 2e LIGNE, pas un écrasement
    with store._conn() as conn:
        rows = [tuple(r) for r in conn.execute("SELECT ts,hops,count FROM packet_hops ORDER BY ts")]
    assert rows == [(1000.0, 0, 3), (2000.0, 0, 5)]


def test_record_packet_hops_empty_writes_nothing(tmp_path):
    store = MetricsStore(str(tmp_path / "m.db"))
    store.record_packet_hops({})
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) AS c FROM packet_hops").fetchone()["c"] == 0


def test_packet_hops_history_bins_and_sums_in_sql(tmp_path):
    clock = Clock()
    store = MetricsStore(str(tmp_path / "m.db"), clock=clock)
    # Deux flushes DANS la même tranche de 900 s -> une seule ligne par bucket, sommée.
    for ts, counts in ((1783622100.0, {0: 40, 2: 7}), (1783622400.0, {0: 2, -1: 1})):
        clock.t = ts
        store.record_packet_hops(counts)
    clock.t = 1783623100.0  # tranche suivante
    store.record_packet_hops({0: 1})
    out = store.packet_hops_history(since=0, bin_seconds=900)
    assert out["bin"] == 900  # bin réfléchi dans la réponse
    # bin_start = floor(ts/900)*900 ; triées par bin_start croissant ; PAS de map de noms.
    assert out == {
        "bin": 900,
        "rows": [
            [1783621800, -1, 1], [1783621800, 0, 42], [1783621800, 2, 7],
            [1783622700, 0, 1],
        ],
    }
    assert [r[0] for r in out["rows"]] == sorted(r[0] for r in out["rows"])


def test_packet_hops_history_carries_local_bucket_minus_2(tmp_path):
    # Le domaine hops inclut -2 (LOCAL) et -1 (Inconnu) : le store est agnostique à la valeur,
    # mais on vérifie qu'ils traversent l'agrégat SQL et ressortent triés par bin_start.
    clock = Clock(1783622100.0)
    store = MetricsStore(str(tmp_path / "m.db"), clock=clock)
    store.record_packet_hops({-2: 56, -1: 3, 0: 12, 2: 5})
    out = store.packet_hops_history(since=0, bin_seconds=900)
    assert out == {
        "bin": 900,
        "rows": [[1783621800, -2, 56], [1783621800, -1, 3], [1783621800, 0, 12], [1783621800, 2, 5]],
    }


def test_packet_hops_history_since_filters(tmp_path):
    clock = Clock()
    store = MetricsStore(str(tmp_path / "m.db"), clock=clock)
    clock.t = 1000.0
    store.record_packet_hops({0: 9})
    clock.t = 5000.0
    store.record_packet_hops({3: 4})
    out = store.packet_hops_history(since=2000.0, bin_seconds=60)
    assert out == {"bin": 60, "rows": [[4980, 3, 4]]}


def test_packet_hops_history_empty_db(tmp_path):
    store = MetricsStore(str(tmp_path / "m.db"))
    assert store.packet_hops_history(since=0, bin_seconds=900) == {"bin": 900, "rows": []}


def test_prune_packets_caps_packet_hops_too(tmp_path):
    clock = Clock(0.0)
    store = MetricsStore(str(tmp_path / "m.db"), clock=clock)
    store.record_packet_hops({0: 1})
    store.record_packets({"!vieux": 1})
    store.record_node({"battery_level": 80})
    store.record_link(1)
    clock.t = 40 * 86400  # 40 j plus tard
    store.record_packet_hops({2: 2})
    store.prune_packets(PACKET_RETENTION_SECONDS)
    with store._conn() as conn:
        hops = [r["hops"] for r in conn.execute("SELECT hops FROM packet_hops")]
        others = [
            conn.execute("SELECT count(*) AS c FROM node_metrics").fetchone()["c"],
            conn.execute("SELECT count(*) AS c FROM link_quality").fetchone()["c"],
        ]
    assert hops == [2]  # au-delà de 35 j : purgé, comme packet_counts
    # Les tables gouvernées par retention_days (0 par défaut = tout garder) sont INTACTES.
    assert others == [1, 1]


def test_prune_also_purges_hops_when_retention_is_set(tmp_path):
    # packet_hops est dans _TS_TABLES : une station qui fixe retention_days EN DEÇÀ de 35 j la
    # purge plus tôt via prune(). 35 j est un plafond, pas un plancher.
    clock = Clock(0.0)
    store = MetricsStore(str(tmp_path / "m.db"), clock=clock)
    store.record_packet_hops({0: 1})
    clock.t = 10 * 86400
    store.prune(2 * 86400)
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) AS c FROM packet_hops").fetchone()["c"] == 0


def test_export_csv_includes_packet_hops(tmp_path):
    store = MetricsStore(str(tmp_path / "m.db"), clock=Clock(1000.0))
    store.record_packet_hops({-1: 3})
    out = tmp_path / "csv"
    store.export_csv(str(out))
    assert (out / "packet_hops.csv").exists()
    assert "-1" in (out / "packet_hops.csv").read_text()


def test_packet_hops_table_created_on_a_preexisting_db(tmp_path):
    # Une table NEUVE n'a pas besoin d'entrée _MIGRATIONS : CREATE TABLE IF NOT EXISTS la crée
    # sur une base déjà en prod. Régression de la classe de bug du crash-loop v0.9.0.
    path = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE node_metrics (ts REAL, battery_level INTEGER)")
    conn.commit()
    conn.close()
    store = MetricsStore(path, clock=Clock(1783622100.0))
    store.record_packet_hops({1: 1})  # le chemin qui crasherait si la table manquait
    assert store.packet_hops_history(since=0) == {"bin": 300, "rows": [[1783622100, 1, 1]]}
