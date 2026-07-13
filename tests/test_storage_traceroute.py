# SPDX-License-Identifier: AGPL-3.0-or-later
import csv
import os

from mbg.storage import MetricsStore


def _result(request_id, dest, status, source="api", rtt=None, route_to=None, route_back=None):
    return {
        "type": "traceroute", "gateway_node": "!gw", "dest": dest, "request_id": request_id,
        "status": status, "sent_ts": "2026-07-14T00:00:00Z", "recv_ts": None if rtt is None else "2026-07-14T00:00:04Z",
        "rtt_ms": rtt, "hop_limit": 7, "hops_to": 2 if route_to else None, "hops_back": None,
        "route_to": route_to, "route_back": route_back, "source": source,
    }


def test_record_and_history_roundtrip(tmp_path):
    store = MetricsStore(str(tmp_path / "t.db"))
    route_to = [{"node": "!gw", "snr": None}, {"node": "!bb", "snr": -6.0}]
    store.record_traceroute(_result(1, "!bb", "ok", rtt=4000, route_to=route_to), 1000.0, 1004.0)
    store.record_traceroute(_result(2, "!cc", "timeout"), 2000.0, None)
    rows = store.traceroute_history()
    assert [r["request_id"] for r in rows] == [2, 1]  # DESC par sent_epoch
    ok = rows[1]
    assert ok["status"] == "ok" and ok["rtt_ms"] == 4000
    assert ok["route_to"] == route_to and ok["route_back"] is None
    # since filtre
    assert [r["request_id"] for r in store.traceroute_history(since=1500.0)] == [2]


def test_history_null_route_json(tmp_path):
    store = MetricsStore(str(tmp_path / "t.db"))
    r = _result(1, "!bb", "error")
    r["route_to"] = None
    store.record_traceroute(r, 1000.0, None)
    row = store.traceroute_history()[0]
    assert row["route_to"] is None and row["route_back"] is None


def test_by_request_id(tmp_path):
    store = MetricsStore(str(tmp_path / "t.db"))
    assert store.traceroute_by_request_id(42) is None
    store.record_traceroute(_result(42, "!bb", "ok", rtt=100, route_to=[{"node": "!bb", "snr": None}]), 1000.0, 1000.1)
    row = store.traceroute_by_request_id(42)
    assert row["status"] == "ok" and row["dest"] == "!bb"


def test_scheduler_state_queries(tmp_path):
    store = MetricsStore(str(tmp_path / "t.db"))
    assert store.traceroute_last_sent() is None
    assert store.traceroute_last_attempt_by_node("!bb") is None
    assert store.traceroute_last_success_by_node() == {}
    store.record_traceroute(_result(1, "!bb", "ok", rtt=1, route_to=[{"node": "!bb", "snr": None}]), 1000.0, 1000.1)
    store.record_traceroute(_result(2, "!bb", "timeout"), 2000.0, None)
    store.record_traceroute(_result(3, "!cc", "ok", source="scheduler:staleness", rtt=2, route_to=[{"node": "!cc", "snr": None}]), 3000.0, 3000.1)
    assert store.traceroute_last_sent() == 3000.0
    assert store.traceroute_last_attempt_by_node("!bb") == 2000.0
    succ = store.traceroute_last_success_by_node()
    assert succ == {"!bb": 1000.0, "!cc": 3000.0}
    # budget : count depuis un seuil, filtré par source
    assert store.traceroute_count_since(0.0) == 3
    assert store.traceroute_count_since(0.0, source_prefix="scheduler:") == 1
    assert store.traceroute_count_since(2500.0) == 1


def test_counters(tmp_path):
    store = MetricsStore(str(tmp_path / "t.db"))
    empty = store.traceroute_counters()
    assert empty == {
        "traceroute_sent_total": 0, "traceroute_ok_total": 0,
        "traceroute_timeout_total": 0, "traceroute_error_total": 0,
        "traceroute_last_rtt_ms": None,
    }
    store.record_traceroute(_result(1, "!bb", "ok", rtt=100, route_to=[{"node": "!bb", "snr": None}]), 1000.0, 1000.1)
    store.record_traceroute(_result(2, "!cc", "timeout"), 2000.0, None)
    store.record_traceroute(_result(3, "!dd", "error"), 3000.0, None)
    store.record_traceroute(_result(4, "!ee", "ok", rtt=250, route_to=[{"node": "!ee", "snr": None}]), 4000.0, 4000.25)
    c = store.traceroute_counters()
    assert c["traceroute_sent_total"] == 4 and c["traceroute_ok_total"] == 2
    assert c["traceroute_timeout_total"] == 1 and c["traceroute_error_total"] == 1
    assert c["traceroute_last_rtt_ms"] == 250  # dernier ok


def test_prune_and_export(tmp_path):
    store = MetricsStore(str(tmp_path / "t.db"), clock=lambda: 10_000.0)
    store.record_traceroute(_result(1, "!bb", "ok", rtt=1, route_to=[{"node": "!bb", "snr": None}]), 1000.0, 1000.1)
    store.record_traceroute(_result(2, "!cc", "ok", rtt=2, route_to=[{"node": "!cc", "snr": None}]), 9000.0, 9000.1)
    store.prune(older_than_seconds=2000)  # cutoff = 10000-2000 = 8000 -> vire request_id 1
    assert [r["request_id"] for r in store.traceroute_history()] == [2]
    # export CSV inclut la table traceroute
    d = str(tmp_path / "csv")
    store.export_csv(d)
    with open(os.path.join(d, "traceroute.csv")) as f:
        rows = list(csv.reader(f))
    assert rows[0][1] == "request_id"  # id, request_id, dest...
    assert len(rows) == 2  # header + 1 ligne restante
