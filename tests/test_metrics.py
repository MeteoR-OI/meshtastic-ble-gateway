# SPDX-License-Identifier: AGPL-3.0-or-later
from types import SimpleNamespace

from mbg import metrics


def test_node_metrics():
    info = {"deviceMetrics": {"batteryLevel": 80, "voltage": 3.9, "channelUtilization": 5.2,
                              "airUtilTx": 1.1, "uptimeSeconds": 12345}}
    m = metrics.node_metrics(info)
    assert m == {"battery_level": 80, "voltage": 3.9, "channel_util": 5.2,
                 "air_util_tx": 1.1, "uptime": 12345}


def test_node_metrics_missing():
    assert metrics.node_metrics({}) == {
        "battery_level": None, "voltage": None, "channel_util": None,
        "air_util_tx": None, "uptime": None,
    }


def test_mqtt_status_none():
    assert metrics.mqtt_status(None) == {
        "mqtt_broker": None, "mqtt_proxy_ok": None, "mqtt_map_reporting": None,
    }


def test_mqtt_status_proxy_ok():
    cfg = SimpleNamespace(address="mqtt-mt.meteor-oi.re", enabled=True,
                          proxy_to_client_enabled=True, map_reporting_enabled=True)
    assert metrics.mqtt_status(cfg) == {
        "mqtt_broker": "mqtt-mt.meteor-oi.re", "mqtt_proxy_ok": True, "mqtt_map_reporting": True,
    }


def test_mqtt_status_enabled_without_proxy():
    cfg = SimpleNamespace(address="autre.broker", enabled=True,
                          proxy_to_client_enabled=False, map_reporting_enabled=False)
    m = metrics.mqtt_status(cfg)
    assert m["mqtt_proxy_ok"] is False and m["mqtt_map_reporting"] is False


def test_mqtt_status_module_disabled_short_circuit():
    # enabled=False -> proxy_ok False même si proxy_to_client_enabled=True
    cfg = SimpleNamespace(address="", enabled=False,
                          proxy_to_client_enabled=True, map_reporting_enabled=True)
    m = metrics.mqtt_status(cfg)
    assert m["mqtt_proxy_ok"] is False
    assert m["mqtt_broker"] is None  # adresse vide -> None (pas de broker configuré)


def test_position():
    info = {"position": {"latitude": -21.3, "longitude": 55.4, "altitude": 289}}
    assert metrics.position(info) == {"lat": -21.3, "lon": 55.4, "altitude": 289}


def test_node_identity():
    info = {"user": {"id": "!abcd1234", "longName": "MaBalise", "shortName": "MB"}}
    assert metrics.node_identity(info) == {"node_id": "!abcd1234", "node_name": "MaBalise"}


def test_node_identity_fallbacks():
    # longName absent -> shortName ; user absent -> tout None
    assert metrics.node_identity({"user": {"id": "!x", "shortName": "SN"}}) == {
        "node_id": "!x", "node_name": "SN",
    }
    assert metrics.node_identity({}) == {"node_id": None, "node_name": None}


def test_neighbors_includes_hops_and_self_excluded():
    nodes = {
        1: {"hopsAway": 0, "user": {"id": "!001"}, "snr": 6.0, "rssi": -90, "lastHeard": 10,
            "position": {"latitude": -21.0, "longitude": 55.5}},
        2: {"hopsAway": 2, "user": {"id": "!002"}},  # relayé -> INCLUS (PORTÉE v2), hops_away=2
        3: {"hopsAway": 0},  # 0-hop sans user ni position -> id dérivé, lat/lon None
        4: {"user": {"id": "!004"}},  # hopsAway absent -> non classable -> exclu
        9: {"hopsAway": 0},  # self -> exclu
    }
    out = metrics.neighbors(nodes, my_num=9)  # sans filtre temporel
    ids = {n["node_id"] for n in out}
    assert ids == {"!001", "!002", "!00000003"}  # relayé inclus, self + hops-inconnu exclus
    direct = next(n for n in out if n["node_id"] == "!001")
    assert direct["snr"] == 6.0 and direct["rssi"] == -90 and direct["last_heard"] == 10
    assert direct["lat"] == -21.0 and direct["lon"] == 55.5 and direct["hops_away"] == 0
    relayed = next(n for n in out if n["node_id"] == "!002")
    assert relayed["hops_away"] == 2
    without = next(n for n in out if n["node_id"] == "!00000003")
    assert without["lat"] is None and without["lon"] is None


def test_resolve_active_window():
    # override>0 prime ; sinon max(monitor_interval, plancher 3600)
    assert metrics.resolve_active_window(300, override=0) == 3600.0  # échantillonnage rapide -> plancher
    assert metrics.resolve_active_window(7200, override=0) == 7200.0  # sonde lente -> couvre 1 cycle
    assert metrics.resolve_active_window(300, override=900) == 900.0  # override explicite


def test_neighbors_active_filter():
    nodes = {
        1: {"hopsAway": 0, "user": {"id": "!frais"}, "lastHeard": 950},   # récent -> gardé
        2: {"hopsAway": 0, "user": {"id": "!perime"}, "lastHeard": 100},  # périmé -> exclu
        3: {"hopsAway": 0, "user": {"id": "!sansts"}},                    # pas de lastHeard -> exclu
    }
    # now=1000, fenêtre=100 -> cutoff=900 : seul !frais (950>=900) passe
    out = metrics.neighbors(nodes, my_num=9, now=1000.0, active_window=100.0)
    assert {n["node_id"] for n in out} == {"!frais"}


def test_neighbors_active_filter_needs_both_params():
    nodes = {1: {"hopsAway": 0, "user": {"id": "!x"}, "lastHeard": 1}}  # très vieux
    # un seul des deux paramètres -> PAS de filtre (compat), le node périmé reste
    assert metrics.neighbors(nodes, my_num=9, now=1e12)[0]["node_id"] == "!x"
    assert metrics.neighbors(nodes, my_num=9, active_window=1.0)[0]["node_id"] == "!x"


def test_haversine_known_distance():
    # Paris ↔ Londres ≈ 344 km (référence géodésique).
    d = metrics._haversine_km(48.8566, 2.3522, 51.5074, -0.1278)
    assert abs(d - 344) < 2


def test_max_distance_km_picks_farthest():
    gateway = {"lat": -21.0, "lon": 55.5}
    neighbors = [
        {"lat": -21.05, "lon": 55.5},   # ~5,6 km
        {"lat": -21.0, "lon": 55.5},    # 0 km (même point)
        {"lat": None, "lon": None},     # sans position -> ignoré
    ]
    d = metrics.max_distance_km(gateway, neighbors)
    assert d == round(metrics._haversine_km(-21.0, 55.5, -21.05, 55.5), 1)
    assert d > 5.0  # le plus lointain, pas le plus proche


def test_max_distance_km_null_when_gateway_has_no_position():
    assert metrics.max_distance_km({"lat": None, "lon": 55.5},
                                   [{"lat": -21.0, "lon": 55.5}]) is None
    assert metrics.max_distance_km({"lat": -21.0, "lon": None},
                                   [{"lat": -21.0, "lon": 55.5}]) is None


def test_max_distance_km_null_when_no_neighbor_has_position():
    gateway = {"lat": -21.0, "lon": 55.5}
    assert metrics.max_distance_km(gateway, [{"lat": None, "lon": None}]) is None
    assert metrics.max_distance_km(gateway, []) is None
