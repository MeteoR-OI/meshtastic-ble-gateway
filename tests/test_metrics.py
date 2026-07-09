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


def test_position():
    info = {"position": {"latitude": -21.3, "longitude": 55.4, "altitude": 289}}
    assert metrics.position(info) == {"lat": -21.3, "lon": 55.4, "altitude": 289}


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
