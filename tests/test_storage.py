# SPDX-License-Identifier: AGPL-3.0-or-later
import os
import sqlite3

from mbg.storage import MetricsStore


def test_record_and_latest(tmp_path):
    store = MetricsStore(str(tmp_path / "m.db"), clock=lambda: 100.0)
    store.record_node(
        {"battery_level": 80, "voltage": 3.9, "node_id": "!abcd", "node_name": "MonNode"},
        {"lat": -21.3, "lon": 55.4},
    )
    store.record_link(3)
    latest = store.latest()
    assert latest["node"]["battery_level"] == 80
    assert latest["node"]["voltage"] == 3.9
    assert latest["node"]["lat"] == -21.3
    assert latest["node"]["node_id"] == "!abcd" and latest["node"]["node_name"] == "MonNode"
    assert latest["link"]["reconnects"] == 3


def test_record_and_latest_mqtt_status(tmp_path):
    store = MetricsStore(str(tmp_path / "m.db"))
    store.record_node(
        {"battery_level": 80, "mqtt_broker": "mqtt-mt.meteor-oi.re",
         "mqtt_proxy_ok": True, "mqtt_map_reporting": False},
    )
    node = store.latest()["node"]
    assert node["mqtt_broker"] == "mqtt-mt.meteor-oi.re"
    assert node["mqtt_proxy_ok"] == 1 and node["mqtt_map_reporting"] == 0  # bool -> 0/1 SQLite


def test_migrates_legacy_db(tmp_path):
    # Base créée AVANT les colonnes MQTT (schéma v0.7) : l'init doit les ajouter
    # (CREATE TABLE IF NOT EXISTS n'ajoute jamais de colonne).
    path = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE node_metrics (ts REAL, battery_level INTEGER, voltage REAL,"
        " channel_util REAL, air_util_tx REAL, uptime INTEGER, lat REAL, lon REAL,"
        " altitude INTEGER, node_id TEXT, node_name TEXT)"
    )
    conn.execute("INSERT INTO node_metrics (ts, battery_level) VALUES (1.0, 42)")
    conn.commit()
    conn.close()
    store = MetricsStore(path)
    store.record_node({"battery_level": 50, "mqtt_broker": "b", "mqtt_proxy_ok": True})
    node = store.latest()["node"]
    assert node["battery_level"] == 50 and node["mqtt_broker"] == "b"
    # l'ancienne ligne survit, colonnes migrées à NULL
    first = store.history()[-1]
    assert first["battery_level"] == 42 and first["mqtt_broker"] is None


def test_latest_empty(tmp_path):
    store = MetricsStore(str(tmp_path / "m.db"))
    assert store.latest() == {"node": None, "link": None, "neighbors": None}


def test_latest_neighbors_aggregate(tmp_path):
    store = MetricsStore(str(tmp_path / "m.db"), clock=lambda: 50.0)
    store.record_neighbors([
        {"node_id": "!aa", "snr": 5.0, "rssi": -100, "last_heard": 1},
        {"node_id": "!bb", "snr": 8.5, "rssi": -90, "last_heard": 2},
    ])
    nb = store.latest()["neighbors"]
    assert nb["count"] == 2 and nb["best_snr"] == 8.5  # dernier batch : count + max snr
    assert nb["max_distance_km"] is None  # aucun relevé node_metrics
    assert nb["distinct_1h"] == 2 and nb["distinct_24h"] == 2 and nb["distinct_total"] == 2


def test_latest_neighbors_max_distance_from_last_node_row(tmp_path):
    store = MetricsStore(str(tmp_path / "m.db"), clock=lambda: 100.0)
    store.record_node({"battery_level": 80, "max_distance_km": 12.4})
    store.record_neighbors([{"node_id": "!aa", "snr": 5.0}])
    nb = store.latest()["neighbors"]
    assert nb["max_distance_km"] == 12.4  # porté par le dernier node_metrics
    assert store.latest()["node"]["max_distance_km"] == 12.4  # aussi lisible sur la ligne node


def test_latest_distinct_windows(tmp_path):
    now = 1_000_000.0
    ticks = iter([now, now - 1800, now - 40000, now - 200000, now])
    #             batch récent  dans 1h    dans 24h     hors 24h      clock de latest()
    store = MetricsStore(str(tmp_path / "m.db"), clock=lambda: next(ticks))
    store.record_neighbors([{"node_id": "!recent"}])           # ts=now (le "dernier batch")
    store.record_neighbors([{"node_id": "!h1"}])               # ts dans 1h
    store.record_neighbors([{"node_id": "!h24"}])              # ts dans 24h (hors 1h)
    store.record_neighbors([{"node_id": "!old"}, {"node_id": "!recent"}])  # ts hors 24h (+ doublon)
    nb = store.latest()["neighbors"]  # clock=now
    assert nb["distinct_1h"] == 2      # !recent + !h1 (ts > now-3600)
    assert nb["distinct_24h"] == 3     # + !h24 (ts > now-86400)
    assert nb["distinct_total"] == 4   # + !old ; !recent (2 occurrences) dédoublonné


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
