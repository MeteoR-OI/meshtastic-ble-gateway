# SPDX-License-Identifier: AGPL-3.0-or-later
from mbg.api import handle_request


def _hdr(token=None):
    return {"X-API-Token": token} if token is not None else {}


def _ok(command):
    return {"ok": True, "detail": "done"}


def test_unauthorized_missing_token():
    status, body = handle_request("GET", "/health", _hdr(), "", "secret", _ok)
    assert status == 401 and body["ok"] is False


def test_unauthorized_wrong_token():
    status, _ = handle_request("GET", "/health", _hdr("nope"), "", "secret", _ok)
    assert status == 401


def test_health():
    status, body = handle_request("GET", "/health", _hdr("s"), "", "s", _ok)
    assert status == 200 and body["status"] == "up"


def test_send_text_builds_command():
    seen = {}

    def dispatch(cmd):
        seen["cmd"] = cmd
        return {"ok": True}

    status, _ = handle_request(
        "POST", "/send/text", _hdr("s"), '{"text":"hi","channel":"meteo","want_ack":true}', "s", dispatch
    )
    assert status == 200
    assert seen["cmd"] == {
        "type": "text", "text": "hi", "channel": "meteo", "dest": None, "want_ack": True,
    }


def test_send_telemetry_empty_body():
    status, _ = handle_request("POST", "/send/telemetry", _hdr("s"), "", "s", lambda c: {"ok": True})
    assert status == 200


def test_send_position_empty_body():
    seen = {}
    status, _ = handle_request(
        "POST", "/send/position", _hdr("s"), "", "s", lambda c: (seen.update(c) or {"ok": True})
    )
    assert status == 200
    assert seen == {"type": "position", "lat": None, "lon": None, "alt": None}  # -> position node


def test_send_position_with_coords():
    seen = {}
    status, _ = handle_request(
        "POST", "/send/position", _hdr("s"), '{"lat":1.5,"lon":2.5,"alt":10}', "s",
        lambda c: (seen.update(c) or {"ok": True}),
    )
    assert status == 200 and seen["lat"] == 1.5 and seen["lon"] == 2.5 and seen["alt"] == 10


def test_admin_route():
    seen = {}
    status, _ = handle_request(
        "POST", "/admin", _hdr("s"), '{"setting":"role","value":"ROUTER"}', "s",
        lambda c: (seen.update(c) or {"ok": True}),
    )
    assert status == 200 and seen["setting"] == "role" and seen["value"] == "ROUTER"


def test_invalid_json():
    status, body = handle_request("POST", "/send/text", _hdr("s"), "{bad", "s", _ok)
    assert status == 400 and "JSON" in body["error"]


def test_unknown_post_route():
    status, _ = handle_request("POST", "/nope", _hdr("s"), "{}", "s", _ok)
    assert status == 404


def test_unknown_method():
    status, _ = handle_request("PUT", "/x", _hdr("s"), "", "s", _ok)
    assert status == 404


def test_get_unknown_route():
    status, _ = handle_request("GET", "/nope", _hdr("s"), "", "s", _ok)
    assert status == 404


class FakeMetrics:
    def latest(self):
        return {"node": {"battery_level": 80}, "link": {"reconnects": 3}}

    def history(self, since, limit):
        self.args = (since, limit)
        return [{"ts": 10, "battery_level": 80}]


def test_metrics_route():
    status, body = handle_request("GET", "/metrics", _hdr("s"), "", "s", _ok, metrics=FakeMetrics())
    assert status == 200 and body["node"]["battery_level"] == 80


def test_metrics_disabled_404():
    status, _ = handle_request("GET", "/metrics", _hdr("s"), "", "s", _ok, metrics=None)
    assert status == 404


def test_history_route_parses_query():
    m = FakeMetrics()
    status, body = handle_request("GET", "/history?since=50&limit=10", _hdr("s"), "", "s", _ok, metrics=m)
    assert status == 200 and body["rows"][0]["battery_level"] == 80
    assert m.args == (50.0, 10)


def test_history_bad_query_400():
    status, _ = handle_request("GET", "/history?since=abc", _hdr("s"), "", "s", _ok, metrics=FakeMetrics())
    assert status == 400


def test_history_disabled_404():
    status, _ = handle_request("GET", "/history", _hdr("s"), "", "s", _ok, metrics=None)
    assert status == 404


def test_status_no_worker_503():
    status, _ = handle_request(
        "POST", "/send/telemetry", _hdr("s"), "{}", "s",
        lambda c: {"ok": False, "error": "aucun worker connecté"},
    )
    assert status == 503


def test_status_timeout_504():
    status, _ = handle_request(
        "POST", "/send/telemetry", _hdr("s"), "{}", "s",
        lambda c: {"ok": False, "error": "timeout worker"},
    )
    assert status == 504


def test_status_bad_command_400():
    status, _ = handle_request(
        "POST", "/admin", _hdr("s"), "{}", "s",
        lambda c: {"ok": False, "error": "réglage admin inconnu: None"},
    )
    assert status == 400
