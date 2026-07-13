# SPDX-License-Identifier: AGPL-3.0-or-later
import json

from mbg.api import TracerouteReader, handle_request


def _hdr(token="s"):
    return {"X-API-Token": token}


class FakeTracerouteReader:
    def __init__(self, wait_row="unset", history=None, counters=None):
        self._wait_row = wait_row
        self._history = history if history is not None else [{"request_id": 1}]
        self._counters = counters or {"traceroute_sent_total": 3}
        self.wait_calls = []

    def wait(self, request_id, timeout_s):
        self.wait_calls.append((request_id, timeout_s))
        return None if self._wait_row == "unset" else self._wait_row

    def history(self, since, limit):
        return self._history

    def counters(self):
        return self._counters


# --- Validation --------------------------------------------------------------
def test_traceroute_invalid_dest():
    status, body = handle_request("POST", "/traceroute", _hdr(), '{"dest":"xyz"}', "s", lambda c: {"ok": True})
    assert status == 400 and body["ok"] is False


def test_traceroute_missing_dest():
    status, body = handle_request("POST", "/traceroute", _hdr(), "{}", "s", lambda c: {"ok": True})
    assert status == 400


def test_traceroute_bad_hop_limit():
    for hop in (0, 8, "x", True):
        body = json.dumps({"dest": "!6984ddb0", "hop_limit": hop})
        status, _ = handle_request("POST", "/traceroute", _hdr(), body, "s", lambda c: {"ok": True})
        assert status == 400


def test_traceroute_bad_channel_index():
    body = json.dumps({"dest": "!6984ddb0", "channel_index": -1})
    status, _ = handle_request("POST", "/traceroute", _hdr(), body, "s", lambda c: {"ok": True})
    assert status == 400


def test_traceroute_bad_timeout():
    for to in (4, 61, "x", True):
        body = json.dumps({"dest": "!6984ddb0", "timeout_s": to})
        status, _ = handle_request("POST", "/traceroute", _hdr(), body, "s", lambda c: {"ok": True})
        assert status == 400


# --- Async (202) -------------------------------------------------------------
def test_traceroute_async_accepted():
    seen = {}

    def dispatch(cmd):
        seen["cmd"] = cmd
        return {"ok": True, "request_id": 777, "dest": "!6984ddb0"}

    body = json.dumps({"dest": "6984ddb0", "hop_limit": 5, "timeout_s": 20})
    status, resp = handle_request("POST", "/traceroute", _hdr(), body, "s", dispatch)
    assert status == 202 and resp == {"status": "accepted", "dest": "!6984ddb0", "request_id": 777}
    assert seen["cmd"] == {
        "type": "traceroute", "dest": "!6984ddb0", "hop_limit": 5,
        "channel_index": 0, "timeout_s": 20.0, "source": "api",
    }


def test_traceroute_dispatch_error():
    status, body = handle_request(
        "POST", "/traceroute", _hdr(), '{"dest":"!6984ddb0"}', "s",
        lambda c: {"ok": False, "error": "aucun worker connecté"},
    )
    assert status == 503 and body["ok"] is False


# --- Mode wait ---------------------------------------------------------------
def test_traceroute_wait_success():
    reader = FakeTracerouteReader(wait_row={"status": "ok", "dest": "!6984ddb0", "rtt_ms": 4000})
    body = json.dumps({"dest": "!6984ddb0", "wait": True, "timeout_s": 15})
    status, resp = handle_request(
        "POST", "/traceroute", _hdr(), body, "s",
        lambda c: {"ok": True, "request_id": 5, "dest": "!6984ddb0"}, traceroute=reader,
    )
    assert status == 200 and resp["status"] == "ok"
    assert reader.wait_calls == [(5, 15.0)]


def test_traceroute_wait_timeout_row():
    reader = FakeTracerouteReader(wait_row={"status": "timeout", "dest": "!6984ddb0"})
    body = json.dumps({"dest": "!6984ddb0", "wait": True})
    status, resp = handle_request(
        "POST", "/traceroute", _hdr(), body, "s",
        lambda c: {"ok": True, "request_id": 5, "dest": "!6984ddb0"}, traceroute=reader,
    )
    assert status == 504 and resp["status"] == "timeout"


def test_traceroute_wait_no_row():
    reader = FakeTracerouteReader(wait_row="unset")  # wait renvoie None
    body = json.dumps({"dest": "!6984ddb0", "wait": True})
    status, resp = handle_request(
        "POST", "/traceroute", _hdr(), body, "s",
        lambda c: {"ok": True, "request_id": 5, "dest": "!6984ddb0"}, traceroute=reader,
    )
    assert status == 504 and resp == {"status": "timeout", "dest": "!6984ddb0", "request_id": 5}


def test_traceroute_wait_no_reader():
    # wait demandé mais aucun reader (store absent) -> 504 propre
    body = json.dumps({"dest": "!6984ddb0", "wait": True})
    status, resp = handle_request(
        "POST", "/traceroute", _hdr(), body, "s",
        lambda c: {"ok": True, "request_id": 5, "dest": "!6984ddb0"}, traceroute=None,
    )
    assert status == 504 and resp["status"] == "timeout"


# --- GET /history?type=traceroute & /metrics counters ------------------------
def test_history_traceroute():
    reader = FakeTracerouteReader(history=[{"request_id": 9, "status": "ok"}])
    status, body = handle_request(
        "GET", "/history?type=traceroute&since=100&limit=5", _hdr(), "", "s",
        lambda c: {}, traceroute=reader,
    )
    assert status == 200 and body["rows"][0]["request_id"] == 9


def test_history_traceroute_disabled():
    status, body = handle_request("GET", "/history?type=traceroute", _hdr(), "", "s", lambda c: {}, traceroute=None)
    assert status == 404 and "traceroute" in body["error"]


def test_history_traceroute_bad_params():
    reader = FakeTracerouteReader()
    status, _ = handle_request(
        "GET", "/history?type=traceroute&since=abc", _hdr(), "", "s", lambda c: {}, traceroute=reader
    )
    assert status == 400


class FakeMetrics:
    def latest(self):
        return {"node": {"battery_level": 80}}

    def history(self, since, limit):
        return []


def test_metrics_includes_traceroute_counters():
    reader = FakeTracerouteReader(counters={"traceroute_sent_total": 7})
    status, body = handle_request(
        "GET", "/metrics", _hdr(), "", "s", lambda c: {}, metrics=FakeMetrics(), traceroute=reader
    )
    assert status == 200 and body["traceroute"]["traceroute_sent_total"] == 7


def test_metrics_without_traceroute():
    status, body = handle_request("GET", "/metrics", _hdr(), "", "s", lambda c: {}, metrics=FakeMetrics())
    assert status == 200 and "traceroute" not in body


# --- TracerouteReader --------------------------------------------------------
class FakeStore:
    def __init__(self, rows_over_time):
        self._rows = list(rows_over_time)
        self.reads = 0

    def traceroute_by_request_id(self, request_id):
        val = self._rows[min(self.reads, len(self._rows) - 1)]
        self.reads += 1
        return val

    def traceroute_history(self, since, limit):
        return [{"since": since, "limit": limit}]

    def traceroute_counters(self):
        return {"traceroute_sent_total": 1}


def test_reader_wait_polls_until_row():
    # None, None, puis la ligne terminale au 3e sondage
    store = FakeStore([None, None, {"status": "ok", "request_id": 5}])
    slept = []
    clk = iter([0.0, 0.1, 0.2, 0.3, 0.4, 0.5])
    reader = TracerouteReader(store, sleep=slept.append, clock=lambda: next(clk), poll_interval=0.5, margin=5.0)
    row = reader.wait(5, timeout_s=30)
    assert row["status"] == "ok"
    assert len(slept) == 2  # 2 sondages vides avant succès


def test_reader_wait_deadline():
    store = FakeStore([None])  # jamais de ligne
    clk = iter([0.0, 100.0])  # 2e lecture dépasse deadline (30+5)
    reader = TracerouteReader(store, sleep=lambda s: None, clock=lambda: next(clk))
    assert reader.wait(5, timeout_s=30) is None


def test_reader_history_and_counters():
    store = FakeStore([None])
    reader = TracerouteReader(store)
    assert reader.history(10.0, 50) == [{"since": 10.0, "limit": 50}]
    assert reader.counters() == {"traceroute_sent_total": 1}
