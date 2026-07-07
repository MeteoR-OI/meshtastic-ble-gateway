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
