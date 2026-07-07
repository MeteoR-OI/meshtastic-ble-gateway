# SPDX-License-Identifier: AGPL-3.0-or-later
from mbg.systemd_notify import sd_notify


class FakeSock:
    def __init__(self, raise_on_connect=False):
        self.connected = None
        self.sent = None
        self.closed = False
        self._raise = raise_on_connect

    def connect(self, path):
        if self._raise:
            raise OSError("refused")
        self.connected = path

    def sendall(self, data):
        self.sent = data

    def close(self):
        self.closed = True


def test_noop_without_notify_socket():
    assert sd_notify("READY=1", env={}) is False


def test_uses_os_environ_when_env_none(monkeypatch):
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    assert sd_notify("READY=1") is False


def test_sends_on_regular_path():
    sock = FakeSock()
    ok = sd_notify(
        "WATCHDOG=1",
        env={"NOTIFY_SOCKET": "/run/systemd/notify"},
        socket_factory=lambda fam, typ: sock,
    )
    assert ok is True
    assert sock.connected == "/run/systemd/notify"
    assert sock.sent == b"WATCHDOG=1"
    assert sock.closed is True


def test_abstract_namespace_socket():
    sock = FakeSock()
    ok = sd_notify(
        "READY=1",
        env={"NOTIFY_SOCKET": "@abstract"},
        socket_factory=lambda fam, typ: sock,
    )
    assert ok is True
    assert sock.connected == "\0abstract"  # '@' -> octet nul


def test_returns_false_on_oserror():
    sock = FakeSock(raise_on_connect=True)
    ok = sd_notify(
        "READY=1",
        env={"NOTIFY_SOCKET": "/run/x"},
        socket_factory=lambda fam, typ: sock,
    )
    assert ok is False
    assert sock.closed is True  # fermé même en erreur (finally)
