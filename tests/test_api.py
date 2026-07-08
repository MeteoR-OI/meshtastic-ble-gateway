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
