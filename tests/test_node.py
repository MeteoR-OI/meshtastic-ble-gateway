# SPDX-License-Identifier: AGPL-3.0-or-later
import meshtastic.ble_interface as ble_mod
import pubsub

from fakes import FakeIface
from mbg.node import (
    CONNECTION_LOST_TOPIC,
    PROXY_TOPIC,
    MeshtasticNodeLink,
    default_interface_factory,
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
    assert unsub == [PROXY_TOPIC, CONNECTION_LOST_TOPIC]


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
