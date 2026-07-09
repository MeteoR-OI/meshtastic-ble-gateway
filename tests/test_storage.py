# SPDX-License-Identifier: AGPL-3.0-or-later
import os

from mbg.storage import MetricsStore


def test_record_and_latest(tmp_path):
    store = MetricsStore(str(tmp_path / "m.db"), clock=lambda: 100.0)
    store.record_node({"battery_level": 80, "voltage": 3.9}, {"lat": -21.3, "lon": 55.4})
    store.record_link(3)
    latest = store.latest()
    assert latest["node"]["battery_level"] == 80
    assert latest["node"]["voltage"] == 3.9
    assert latest["node"]["lat"] == -21.3
    assert latest["link"]["reconnects"] == 3


def test_latest_empty(tmp_path):
    store = MetricsStore(str(tmp_path / "m.db"))
    assert store.latest() == {"node": None, "link": None}


def test_history_and_since(tmp_path):
    ticks = iter([10.0, 20.0, 30.0])
    store = MetricsStore(str(tmp_path / "m.db"), clock=lambda: next(ticks))
    store.record_node({"battery_level": 1})
    store.record_node({"battery_level": 2})
    store.record_node({"battery_level": 3})
    rows = store.history(since=15.0)
    assert [r["battery_level"] for r in rows] == [3, 2]  # ts>=15, desc
    assert store.history(limit=1)[0]["battery_level"] == 3


def test_record_neighbors(tmp_path):
    store = MetricsStore(str(tmp_path / "m.db"), clock=lambda: 50.0)
    store.record_neighbors([{"node_id": "!aa", "snr": 5.0, "rssi": -100, "last_heard": 42}])
    with store._conn() as conn:
        row = conn.execute("SELECT * FROM neighbors").fetchone()
    assert row["node_id"] == "!aa" and row["snr"] == 5.0


def test_prune(tmp_path):
    ticks = iter([10.0, 100.0, 100.0])  # 2 records @10, puis clock=100 pour prune
    store = MetricsStore(str(tmp_path / "m.db"), clock=lambda: next(ticks))
    store.record_node({"battery_level": 1})  # ts=10
    store.prune(older_than_seconds=50)  # clock=100 -> cutoff=50 -> supprime ts<50
    assert store.history() == []


def test_export_csv(tmp_path):
    store = MetricsStore(str(tmp_path / "m.db"), clock=lambda: 1.0)
    store.record_node({"battery_level": 77})
    outdir = tmp_path / "csv"
    store.export_csv(str(outdir))
    content = (outdir / "node_metrics.csv").read_text()
    assert "battery_level" in content and "77" in content
    # table vide -> fichier créé mais sans en-tête
    assert os.path.exists(outdir / "neighbors.csv")
    assert (outdir / "neighbors.csv").read_text() == ""
