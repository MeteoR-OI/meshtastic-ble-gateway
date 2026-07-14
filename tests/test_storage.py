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


# Schéma node_metrics de la toute 1re release (0.3/0.4/0.6.x) : AUCUNE des colonnes ajoutées
# ensuite (node_id, node_name, mqtt_*, max_distance_km). Reproduit la base de CHAR645/MHA235.
_LEGACY_0_6 = (
    "CREATE TABLE node_metrics (ts REAL, battery_level INTEGER, voltage REAL,"
    " channel_util REAL, air_util_tx REAL, uptime INTEGER, lat REAL, lon REAL, altitude INTEGER)"
)


def test_migrates_legacy_db(tmp_path):
    # Base 0.6.x SANS node_id/node_name/mqtt_*/max_distance_km : l'init doit TOUTES les ajouter
    # (CREATE TABLE IF NOT EXISTS n'ajoute jamais de colonne). Régression du crash-loop v0.9.0
    # `table node_metrics has no column named node_id` (CHAR645).
    path = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(path)
    conn.execute(_LEGACY_0_6)
    conn.execute("INSERT INTO node_metrics (ts, battery_level) VALUES (1.0, 42)")
    conn.commit()
    conn.close()
    store = MetricsStore(path)
    # colonnes toutes migrées
    with store._conn() as c:
        cols = {r["name"] for r in c.execute("PRAGMA table_info(node_metrics)")}
    assert {"node_id", "node_name", "mqtt_broker", "max_distance_km"} <= cols
    # record_node avec node_id/node_name RÉUSSIT (le chemin qui crashait en 0.9.0)
    store.record_node(
        {"battery_level": 50, "node_id": "!abcd", "node_name": "Nd", "mqtt_broker": "b", "mqtt_proxy_ok": True}
    )
    node = store.latest()["node"]
    assert node["node_id"] == "!abcd" and node["node_name"] == "Nd" and node["mqtt_broker"] == "b"
    # l'ancienne ligne survit, colonnes migrées à NULL
    first = store.history()[-1]
    assert first["battery_level"] == 42 and first["node_id"] is None and first["mqtt_broker"] is None


def test_migration_idempotent_on_migrated_db(tmp_path):
    # Ré-ouvrir une base déjà migrée ne doit ni échouer ni re-ajouter de colonne.
    path = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(path)
    conn.execute(_LEGACY_0_6)
    conn.commit()
    conn.close()
    MetricsStore(path)  # 1re migration
    store2 = MetricsStore(path)  # 2e ouverture : PRAGMA voit les colonnes -> aucun ALTER, pas d'erreur
    store2.record_node({"battery_level": 1, "node_id": "!aa"})
    assert store2.latest()["node"]["node_id"] == "!aa"


def test_seeds_registry_from_legacy_neighbors(tmp_path):
    # Base ≤ v0.8.2 : la table snapshot `neighbors` doit graine le registre pour préserver
    # distinct_total (continuité du compteur au 1er démarrage PORTÉE v2).
    path = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE neighbors (ts REAL, node_id TEXT, snr REAL, rssi INTEGER, last_heard INTEGER)")
    conn.executemany(
        "INSERT INTO neighbors (ts, node_id, last_heard) VALUES (?,?,?)",
        [(1.0, "!aa", 900), (2.0, "!aa", 950), (3.0, "!bb", 800), (4.0, None, 700)],
    )
    conn.commit()
    conn.close()
    store = MetricsStore(path, clock=lambda: 1000.0, active_window=1e9)
    nb = store.latest()["neighbors"]
    assert nb["distinct_total"] == 2  # !aa (dédoublonné, last_heard max) + !bb ; NULL ignoré
    with store._conn() as c:
        row = c.execute("SELECT last_heard FROM neighbor_registry WHERE node_id='!aa'").fetchone()
    assert row["last_heard"] == 950  # max(last_heard) graine


def test_seed_is_idempotent_and_preserves_live_data(tmp_path):
    path = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE neighbors (ts REAL, node_id TEXT, snr REAL, rssi INTEGER, last_heard INTEGER)")
    conn.execute("INSERT INTO neighbors (ts, node_id, last_heard) VALUES (1.0, '!aa', 900)")
    conn.commit()
    conn.close()
    store = MetricsStore(path, clock=lambda: 1000.0, active_window=1e9)
    # un relevé live enrichit !aa (position + hops)
    store.upsert_neighbors([_nbr("!aa", 999, hops_away=0, lat=-21.0, lon=55.5, snr=7.0)])
    # ré-ouverture : le re-seed (INSERT OR IGNORE) ne doit PAS écraser les données live
    store2 = MetricsStore(path, clock=lambda: 1000.0, active_window=1e9)
    with store2._conn() as c:
        row = c.execute("SELECT last_heard, lat FROM neighbor_registry WHERE node_id='!aa'").fetchone()
    assert row["last_heard"] == 999 and row["lat"] == -21.0  # live conservé, pas ré-graine à 900


def test_latest_empty(tmp_path):
    store = MetricsStore(str(tmp_path / "m.db"))
    assert store.latest() == {"node": None, "link": None, "neighbors": None}


def _nbr(node_id, last_heard, *, hops_away=0, snr=None, lat=None, lon=None):
    return {"node_id": node_id, "last_heard": last_heard, "hops_away": hops_away,
            "snr": snr, "lat": lat, "lon": lon}


def test_latest_neighbors_registry_aggregate(tmp_path):
    now = 1_000_000.0
    store = MetricsStore(str(tmp_path / "m.db"), clock=lambda: now)  # active_window défaut 3600
    store.upsert_neighbors([
        _nbr("!aa", now - 100, snr=5.0),
        _nbr("!bb", now - 200, snr=8.5),
    ])
    nb = store.latest()["neighbors"]
    assert nb["count"] == 2 and nb["best_snr"] == 8.5  # actifs : count + max snr
    assert nb["max_distance_km"] is None and nb["max_distance_hops_km"] is None  # pas de position
    assert nb["distinct_1h"] == 2 and nb["distinct_24h"] == 2 and nb["distinct_total"] == 2


def test_latest_neighbors_distances_direct_and_hops(tmp_path):
    now = 1_000_000.0
    store = MetricsStore(str(tmp_path / "m.db"), clock=lambda: now)
    store.record_node({"node_id": "!gw"}, {"lat": -21.0, "lon": 55.5})  # position passerelle
    store.upsert_neighbors([
        _nbr("!direct", now - 10, hops_away=0, lat=-21.05, lon=55.5, snr=5.0),  # ~5,5 km, 0-hop
        _nbr("!colo", now - 10, hops_away=0, lat=-21.0, lon=55.5, snr=4.0),     # 0 km (co-localisé)
        _nbr("!relay", now - 10, hops_away=2, lat=-21.5, lon=55.5, snr=3.0),    # ~55 km, relayé
    ])
    nb = store.latest()["neighbors"]
    assert nb["count"] == 3
    assert 5.0 < nb["max_distance_km"] < 6.0     # DIRECT = le plus lointain des 0-hop (pas le relayé)
    assert 50 < nb["max_distance_hops_km"] < 60  # MULTI-HOP séparé


def test_latest_max_distance_zero_is_valid(tmp_path):
    now = 1_000_000.0
    store = MetricsStore(str(tmp_path / "m.db"), clock=lambda: now)
    store.record_node({}, {"lat": -21.0, "lon": 55.5})
    store.upsert_neighbors([_nbr("!colo", now - 1, hops_away=0, lat=-21.0, lon=55.5, snr=1.0)])
    assert store.latest()["neighbors"]["max_distance_km"] == 0.0  # co-localisé -> 0.0, PAS None


def test_registry_survives_reconnect_and_ages_by_last_heard(tmp_path):
    now = 1_000_000.0
    store = MetricsStore(str(tmp_path / "m.db"), clock=lambda: now, active_window=100.0)
    store.upsert_neighbors([_nbr("!a", now - 10, snr=5.0)])
    # "reconnexion" : nouvelle sonde, !a PAS ré-entendu (conservé) + !b nouveau
    store.upsert_neighbors([_nbr("!b", now - 5, snr=6.0)])
    nb = store.latest()["neighbors"]
    assert nb["count"] == 2 and nb["distinct_total"] == 2  # le registre a survécu (pas de sous-comptage)
    # un périmé (hors fenêtre 100 s) : exclu du count actif mais compté dans l'historique
    store.upsert_neighbors([_nbr("!old", now - 500, snr=1.0)])
    nb2 = store.latest()["neighbors"]
    assert nb2["count"] == 2 and nb2["distinct_total"] == 3


def test_upsert_updates_existing_node(tmp_path):
    now = 1_000_000.0
    store = MetricsStore(str(tmp_path / "m.db"), clock=lambda: now, active_window=100.0)
    store.upsert_neighbors([_nbr("!a", now - 500, snr=2.0)])   # inactif
    assert store.latest()["neighbors"]["count"] == 0          # dtot=1 -> bloc présent, count 0
    store.upsert_neighbors([_nbr("!a", now - 10, snr=9.0)])    # ré-entendu -> maj de la ligne
    nb = store.latest()["neighbors"]
    assert nb["count"] == 1 and nb["best_snr"] == 9.0 and nb["distinct_total"] == 1  # pas de doublon


def test_latest_distinct_windows(tmp_path):
    now = 1_000_000.0
    store = MetricsStore(str(tmp_path / "m.db"), clock=lambda: now, active_window=100.0)
    store.upsert_neighbors([
        _nbr("!recent", now - 10),      # < 100 s : actif + 1h + 24h + total
        _nbr("!h1", now - 1800),        # dans 1h (mais inactif : > 100 s)
        _nbr("!h24", now - 40000),      # dans 24h
        _nbr("!old", now - 200000),     # hors 24h
    ])
    nb = store.latest()["neighbors"]
    assert nb["count"] == 1            # seul !recent est ACTIF (fenêtre 100 s)
    assert nb["distinct_1h"] == 2      # !recent + !h1 (last_heard > now-3600)
    assert nb["distinct_24h"] == 3     # + !h24 (> now-86400)
    assert nb["distinct_total"] == 4   # tout l'historique


def test_history_and_since(tmp_path):
    ticks = iter([10.0, 20.0, 30.0])
    store = MetricsStore(str(tmp_path / "m.db"), clock=lambda: next(ticks))
    store.record_node({"battery_level": 1})
    store.record_node({"battery_level": 2})
    store.record_node({"battery_level": 3})
    rows = store.history(since=15.0)
    assert [r["battery_level"] for r in rows] == [3, 2]  # ts>=15, desc
    assert store.history(limit=1)[0]["battery_level"] == 3


def test_upsert_neighbors_row(tmp_path):
    store = MetricsStore(str(tmp_path / "m.db"), clock=lambda: 50.0)
    store.upsert_neighbors([_nbr("!aa", 42, hops_away=1, snr=5.0, lat=-21.0, lon=55.5)])
    with store._conn() as conn:
        row = conn.execute("SELECT * FROM neighbor_registry").fetchone()
    assert row["node_id"] == "!aa" and row["snr"] == 5.0
    assert row["hops_away"] == 1 and row["lat"] == -21.0


def test_prune_keeps_registry(tmp_path):
    ticks = iter([10.0, 100.0, 100.0])  # record_node@10, prune@100, latest@100
    store = MetricsStore(str(tmp_path / "m.db"), clock=lambda: next(ticks))
    store.record_node({"battery_level": 1})  # ts=10
    store.upsert_neighbors([_nbr("!a", 90.0, snr=5.0)])  # last_heard=90 (registre)
    store.prune(older_than_seconds=50)  # clock=100 -> cutoff=50 -> supprime node_metrics ts<50
    assert store.history() == []  # série temporelle purgée
    # le registre voisins n'est PAS purgé (distinct_total = tout l'historique)
    assert store.latest()["neighbors"]["distinct_total"] == 1


def test_export_csv(tmp_path):
    now = 1_000_000.0
    store = MetricsStore(str(tmp_path / "m.db"), clock=lambda: now)
    store.record_node({"battery_level": 77})
    store.upsert_neighbors([_nbr("!aa", now - 10, snr=5.0)])
    outdir = tmp_path / "csv"
    store.export_csv(str(outdir))
    assert "battery_level" in (outdir / "node_metrics.csv").read_text()
    # le registre voisins est exporté (ordonné par last_heard) ; l'ancienne table snapshot ne l'est plus
    assert "!aa" in (outdir / "neighbor_registry.csv").read_text()
    assert not os.path.exists(outdir / "neighbors.csv")
    # table vide -> fichier créé mais sans en-tête
    assert (outdir / "link_quality.csv").read_text() == ""
