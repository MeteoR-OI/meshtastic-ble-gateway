# SPDX-License-Identifier: AGPL-3.0-or-later
import signal as signal_mod

import pytest

import mbg.__main__ as main_mod
from mbg.__main__ import main


class FakeGateway:
    last = None

    def __init__(self, config, publisher_factory, nodelink_factory, **kwargs):
        self.config = config
        self.publisher_factory = publisher_factory
        self.nodelink_factory = nodelink_factory
        FakeGateway.last = self

    def run(self, should_continue):
        self.should_continue = should_continue
        self.before_stop = should_continue()
        # exerce les fabriques réelles (constructeurs sans I/O)
        self.publisher = self.publisher_factory()
        self.link = self.nodelink_factory("addr", lambda m: None, lambda: None)


@pytest.fixture
def captured_signals(monkeypatch):
    handlers = {}
    monkeypatch.setattr(signal_mod, "signal", lambda sig, h: handlers.__setitem__(sig, h))
    return handlers


@pytest.mark.parametrize("extra", [[], ["-v"]])
def test_main_wires_and_runs(monkeypatch, captured_signals, extra):
    monkeypatch.setattr(main_mod, "Gateway", FakeGateway)
    rc = main(["--broker", "h", "--port", "1884", "--username", "u", "--password", "p"] + extra)
    assert rc == 0

    gw = FakeGateway.last
    assert gw.config.broker_host == "h"
    assert gw.config.broker_port == 1884
    assert gw.config.broker_username == "u"
    assert gw.before_stop is True

    # SIGINT -> _stop -> la boucle doit demander l'arrêt
    assert gw.should_continue() is True
    captured_signals[signal_mod.SIGINT](signal_mod.SIGINT, None)
    assert gw.should_continue() is False


def test_main_reads_env_without_cli_args(monkeypatch, captured_signals):
    """Chemin systemd : aucun argument CLI, tout vient de l'environnement MBG_*."""
    monkeypatch.setattr(main_mod, "Gateway", FakeGateway)
    monkeypatch.setenv("MBG_BLE_ADDRESS", "F9:98:08:73:85:AE")
    monkeypatch.setenv("MBG_BROKER_HOST", "mqtt-mt.example")
    monkeypatch.setenv("MBG_BROKER_PORT", "1884")
    monkeypatch.setenv("MBG_BROKER_USERNAME", "gw-user")
    monkeypatch.setenv("MBG_BROKER_PASSWORD", "secret")

    rc = main([])  # <-- exactement ce que fait `ExecStart=python -m mbg`
    assert rc == 0

    cfg = FakeGateway.last.config
    assert cfg.ble_address == "F9:98:08:73:85:AE"
    assert cfg.broker_host == "mqtt-mt.example"
    assert cfg.broker_port == 1884
    assert cfg.broker_username == "gw-user"
    assert cfg.broker_password == "secret"


def test_cli_args_override_env(monkeypatch, captured_signals):
    """Un argument CLI l'emporte sur l'ENV (usage manuel / PoC)."""
    monkeypatch.setattr(main_mod, "Gateway", FakeGateway)
    monkeypatch.setenv("MBG_BROKER_HOST", "from-env")
    rc = main(["--broker", "from-cli"])
    assert rc == 0
    assert FakeGateway.last.config.broker_host == "from-cli"
