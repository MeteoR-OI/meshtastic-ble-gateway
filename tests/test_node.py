# SPDX-License-Identifier: AGPL-3.0-or-later
from types import SimpleNamespace

import meshtastic.ble_interface as ble_mod
import pubsub

from fakes import FakeIface
from mbg.node import (
    CONNECTION_LOST_TOPIC,
    PROXY_TOPIC,
    RECEIVE_TOPIC,
    MeshtasticNodeLink,
    _ack_status,
    default_interface_factory,
    default_liveness,
    default_subscribe,
    default_unsubscribe,
)


def test_open_subscribes_and_connects():
    subs = []
    made = {}

    def mk(addr):
        made["addr"] = addr
        return FakeIface(addr)

    link = MeshtasticNodeLink(
        "addr",
        lambda m: None,
        interface_factory=mk,
        subscribe=lambda h, t: subs.append((h, t)),
        unsubscribe=lambda h, t: None,
    )
    link.open()
    topics = [t for _, t in subs]
    assert PROXY_TOPIC in topics
    assert CONNECTION_LOST_TOPIC in topics
    assert made["addr"] == "addr"


def test_handler_routes_to_on_proxy():
    received = []
    captured = {}
    link = MeshtasticNodeLink(
        "addr",
        received.append,
        interface_factory=FakeIface,
        subscribe=lambda h, t: captured.setdefault(t, h),
        unsubscribe=lambda h, t: None,
    )
    link.open()
    msg = object()
    captured[PROXY_TOPIC](proxymessage=msg, interface="iface")
    assert received == [msg]


def test_close_after_open():
    ifaces = []
    unsub = []

    def mk(addr):
        i = FakeIface(addr)
        ifaces.append(i)
        return i

    link = MeshtasticNodeLink(
        "addr",
        lambda m: None,
        interface_factory=mk,
        subscribe=lambda h, t: None,
        unsubscribe=lambda h, t: unsub.append((h, t)),
    )
    link.open()
    link.close()
    assert ifaces[0].closed is True
    topics = [t for _, t in unsub]
    assert PROXY_TOPIC in topics
    assert CONNECTION_LOST_TOPIC in topics


def test_close_without_open_skips_iface():
    unsub = []
    link = MeshtasticNodeLink(
        "addr",
        lambda m: None,
        subscribe=lambda h, t: None,
        unsubscribe=lambda h, t: unsub.append(t),
    )
    link.close()  # branche _iface is None
    assert unsub == [PROXY_TOPIC, CONNECTION_LOST_TOPIC, RECEIVE_TOPIC]


def test_handler_lost_routes_to_on_lost():
    lost_called = []
    captured = {}
    link = MeshtasticNodeLink(
        "addr",
        lambda m: None,
        on_lost=lambda: lost_called.append(True),
        interface_factory=FakeIface,
        subscribe=lambda h, t: captured.setdefault(t, h),
        unsubscribe=lambda h, t: None,
    )
    link.open()
    captured[CONNECTION_LOST_TOPIC](interface="iface")
    assert lost_called == [True]


def test_handler_lost_without_callback_is_noop():
    captured = {}
    link = MeshtasticNodeLink(
        "addr",
        lambda m: None,  # on_lost par défaut = None
        interface_factory=FakeIface,
        subscribe=lambda h, t: captured.setdefault(t, h),
        unsubscribe=lambda h, t: None,
    )
    link.open()
    captured[CONNECTION_LOST_TOPIC](interface="iface")  # ne doit pas lever


def test_is_alive_false_when_not_open():
    link = MeshtasticNodeLink("addr", lambda m: None)
    assert link.is_alive() is False  # _iface None


def test_is_alive_delegates_to_liveness():
    link = MeshtasticNodeLink(
        "addr",
        lambda m: None,
        interface_factory=FakeIface,
        subscribe=lambda h, t: None,
        unsubscribe=lambda h, t: None,
        liveness=lambda iface: False,
    )
    link.open()
    assert link.is_alive() is False


def _iface_with_connected(value):
    return SimpleNamespace(client=SimpleNamespace(bleak_client=SimpleNamespace(is_connected=value)))


def test_default_liveness_connected():
    assert default_liveness(_iface_with_connected(True)) is True


def test_default_liveness_disconnected():
    assert default_liveness(_iface_with_connected(False)) is False


def test_default_liveness_fail_open_when_introspection_missing():
    assert default_liveness(SimpleNamespace()) is True  # pas de .client -> fail-open


def test_read_metrics_aggregates():
    iface = SimpleNamespace(
        getMyNodeInfo=lambda: {
            "num": 9,
            "deviceMetrics": {"batteryLevel": 80},
            "position": {"latitude": -21.0},
        },
        nodesByNum={1: {"hopsAway": 0, "user": {"id": "!001"}, "snr": 5.0}},
        client=SimpleNamespace(bleak_client=SimpleNamespace(_properties={"RSSI": -88})),
    )
    link = MeshtasticNodeLink(
        "addr", lambda m: None,
        interface_factory=lambda a: iface,
        subscribe=lambda h, t: None,
        unsubscribe=lambda h, t: None,
    )
    link.open()
    data = link.read_metrics()
    assert data["node"]["battery_level"] == 80
    assert data["node"]["ble_rssi"] == -88
    assert data["position"]["lat"] == -21.0
    assert data["neighbors"][0]["node_id"] == "!001"


def test_send_delegates_to_executor():
    seen = {}

    def fake_executor(iface, command):
        seen["iface"] = iface
        seen["command"] = command
        return {"ok": True, "detail": "envoyé"}

    link = MeshtasticNodeLink(
        "addr",
        lambda m: None,
        interface_factory=FakeIface,
        subscribe=lambda h, t: None,
        unsubscribe=lambda h, t: None,
        executor=fake_executor,
    )
    link.open()
    result = link.send({"type": "text", "text": "hi"})
    assert result == {"ok": True, "detail": "envoyé"}
    assert seen["command"] == {"type": "text", "text": "hi"}
    assert isinstance(seen["iface"], FakeIface)


class FakeTimer:
    def __init__(self, interval, fn):
        self.interval = interval
        self.fn = fn
        self.started = False
        self.cancelled = False
        self.daemon = False

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True


def _ack_link(executor, timers, captured):
    return MeshtasticNodeLink(
        "addr",
        lambda m: None,
        interface_factory=FakeIface,
        subscribe=lambda h, t: captured.__setitem__(t, h),
        unsubscribe=lambda h, t: None,
        executor=executor,
        timer_factory=lambda interval, fn: timers.append(FakeTimer(interval, fn)) or timers[-1],
    )


def test_want_ack_tracked_then_acked():
    timers, captured = [], {}
    link = _ack_link(lambda i, c: {"ok": True, "want_ack": True, "packet_id": 42}, timers, captured)
    link.open()
    r = link.send({"type": "text", "channel": "Fr_Balise", "want_ack": True})
    assert r["ok"] and timers[0].started is True
    # ROUTING_APP avec le bon requestId -> ACK logué + timer annulé
    captured[RECEIVE_TOPIC](
        packet={"decoded": {"portnum": "ROUTING_APP", "requestId": 42, "routing": {"errorReason": "NONE"}}}
    )
    assert timers[0].cancelled is True


def test_receive_ignores_non_routing():
    timers, captured = [], {}
    link = _ack_link(lambda i, c: {"ok": True}, timers, captured)
    link.open()
    captured[RECEIVE_TOPIC](packet={"decoded": {"portnum": "TEXT_MESSAGE_APP"}})  # ignoré


def test_receive_unknown_request_id_ignored():
    timers, captured = [], {}
    link = _ack_link(lambda i, c: {"ok": True}, timers, captured)
    link.open()
    captured[RECEIVE_TOPIC](packet={"decoded": {"portnum": "ROUTING_APP", "requestId": 999}})  # non suivi


def test_ack_timeout_logs_then_noop():
    timers, captured = [], {}
    link = _ack_link(lambda i, c: {"ok": True, "want_ack": True, "packet_id": 7}, timers, captured)
    link.open()
    link.send({"type": "text", "channel": "Fr_Balise", "want_ack": True})
    timers[0].fn()  # timeout -> logue + retire
    timers[0].fn()  # déjà retiré -> no-op


def test_send_without_want_ack_no_tracking():
    timers, captured = [], {}
    link = _ack_link(lambda i, c: {"ok": True}, timers, captured)
    link.open()
    link.send({"type": "telemetry"})
    assert timers == []


def test_send_want_ack_but_no_packet_id():
    timers, captured = [], {}
    link = _ack_link(lambda i, c: {"ok": True, "want_ack": True, "packet_id": None}, timers, captured)
    link.open()
    link.send({"type": "text", "want_ack": True})
    assert timers == []  # pas d'id -> pas de suivi


def test_close_cancels_pending_acks():
    timers, captured = [], {}
    link = _ack_link(lambda i, c: {"ok": True, "want_ack": True, "packet_id": 5}, timers, captured)
    link.open()
    link.send({"type": "text", "channel": "x", "want_ack": True})
    link.close()
    assert timers[0].cancelled is True


def test_ack_status_helper():
    assert _ack_status({"decoded": {"routing": {"errorReason": "NONE"}}}) == "reçu (ACK)"
    assert _ack_status({}) == "reçu (ACK)"
    assert "échec" in _ack_status({"decoded": {"routing": {"errorReason": "MAX_RETRANSMIT"}}})


def test_default_interface_factory(monkeypatch):
    made = {}

    def fake_ble(addr):
        made["addr"] = addr
        return "IFACE"

    monkeypatch.setattr(ble_mod, "BLEInterface", fake_ble)
    assert default_interface_factory("XX") == "IFACE"
    assert made["addr"] == "XX"


def test_default_subscribe_unsubscribe(monkeypatch):
    calls = []
    monkeypatch.setattr(pubsub.pub, "subscribe", lambda h, t: calls.append(("sub", t)))
    monkeypatch.setattr(pubsub.pub, "unsubscribe", lambda h, t: calls.append(("unsub", t)))
    default_subscribe(lambda: None, "topic")
    default_unsubscribe(lambda: None, "topic")
    assert calls == [("sub", "topic"), ("unsub", "topic")]
