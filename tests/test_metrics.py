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


def test_neighbors_filters_direct_and_self():
    nodes = {
        1: {"hopsAway": 0, "user": {"id": "!001"}, "snr": 6.0, "rssi": -90, "lastHeard": 10},
        2: {"hopsAway": 2, "user": {"id": "!002"}},  # via relais -> exclu
        3: {"hopsAway": 0},  # 0-hop sans user -> id dérivé du num
        9: {"hopsAway": 0},  # self -> exclu
    }
    out = metrics.neighbors(nodes, my_num=9)
    ids = {n["node_id"] for n in out}
    assert ids == {"!001", "!00000003"}
    direct = next(n for n in out if n["node_id"] == "!001")
    assert direct["snr"] == 6.0 and direct["rssi"] == -90 and direct["last_heard"] == 10
