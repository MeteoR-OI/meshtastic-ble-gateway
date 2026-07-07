# SPDX-License-Identifier: AGPL-3.0-or-later
import paho.mqtt.client as paho_mod
import pytest

from fakes import FakeClient
from mbg.mqtt_publisher import PahoPublisher, default_client_factory


def test_connect_without_username():
    fc = FakeClient()
    pub = PahoPublisher("h", 1883, client_factory=lambda: fc)
    pub.connect()
    assert ("connect", "h", 1883, 60) in fc.calls
    assert ("loop_start",) in fc.calls
    assert fc.username is None


def test_connect_with_username():
    fc = FakeClient()
    pub = PahoPublisher("h", 1883, "u", "p", client_factory=lambda: fc)
    pub.connect()
    assert fc.username == ("u", "p")


def test_publish_requires_connect():
    pub = PahoPublisher("h", client_factory=FakeClient)
    with pytest.raises(RuntimeError):
        pub.publish("t", b"x")


def test_publish_after_connect():
    fc = FakeClient()
    pub = PahoPublisher("h", client_factory=lambda: fc)
    pub.connect()
    pub.publish("t", b"x")
    assert ("publish", "t", b"x") in fc.calls


def test_close_noop_when_not_connected():
    pub = PahoPublisher("h", client_factory=FakeClient)
    pub.close()  # ne doit pas lever


def test_close_after_connect_clears_client():
    fc = FakeClient()
    pub = PahoPublisher("h", client_factory=lambda: fc)
    pub.connect()
    pub.close()
    assert ("loop_stop",) in fc.calls
    assert ("disconnect",) in fc.calls
    with pytest.raises(RuntimeError):
        pub.publish("t", b"x")


def test_default_client_factory_version2(monkeypatch):
    sentinel = object()
    captured = {}

    class FakeVer:
        VERSION2 = "v2"

    def fake_client(arg):
        captured["arg"] = arg
        return sentinel

    monkeypatch.setattr(paho_mod, "CallbackAPIVersion", FakeVer, raising=False)
    monkeypatch.setattr(paho_mod, "Client", fake_client)
    assert default_client_factory() is sentinel
    assert captured["arg"] == "v2"


def test_default_client_factory_legacy(monkeypatch):
    sentinel = object()

    def fake_client(*args):
        assert args == ()
        return sentinel

    monkeypatch.delattr(paho_mod, "CallbackAPIVersion", raising=False)
    monkeypatch.setattr(paho_mod, "Client", fake_client)
    assert default_client_factory() is sentinel
