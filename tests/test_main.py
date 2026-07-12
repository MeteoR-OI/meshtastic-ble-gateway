# SPDX-License-Identifier: AGPL-3.0-or-later
import signal as signal_mod

import pytest

import mbg.__main__ as main_mod
from mbg.__main__ import _build_serve, main
from mbg.config import Config


class FakeSupervisor:
    last = None

    def __init__(self, config, spawn, **kwargs):
        self.config = config
        self.spawn = spawn
        FakeSupervisor.last = self

    def run(self, should_continue):
        self.should_continue = should_continue
        self.before_stop = should_continue()
        self.spawned = self.spawn(self.config)  # exerce la fabrique spawn_worker(cfg, ctx)


@pytest.fixture
def captured_signals(monkeypatch):
    handlers = {}
    monkeypatch.setattr(signal_mod, "signal", lambda sig, h: handlers.__setitem__(sig, h))
    return handlers


@pytest.fixture(autouse=True)
def _fake_backend(monkeypatch):
    # Évite tout vrai fork / vraie base SQLite.
    monkeypatch.setattr(main_mod, "Supervisor", FakeSupervisor)
    monkeypatch.setattr(main_mod, "spawn_worker", lambda config, ctx: "WORKER")
    monkeypatch.setattr(main_mod, "MetricsStore", lambda path, active_window=None: ("STORE", path))


@pytest.mark.parametrize("extra", [[], ["-v"]])
def test_main_wires_supervisor_and_runs(captured_signals, extra):
    rc = main(["--broker", "h", "--port", "1884", "--username", "u", "--password", "p"] + extra)
    assert rc == 0

    sup = FakeSupervisor.last
    assert sup.config.broker_host == "h"
    assert sup.config.broker_port == 1884
    assert sup.config.broker_username == "u"
    assert sup.spawned == "WORKER"  # la fabrique spawn a bien été exercée
    assert sup.before_stop is True

    assert sup.should_continue() is True
    captured_signals[signal_mod.SIGINT](signal_mod.SIGINT, None)
    assert sup.should_continue() is False


def test_main_reads_env_without_cli_args(captured_signals, monkeypatch):
    monkeypatch.setenv("MBG_BLE_ADDRESS", "F9:98:08:73:85:AE")
    monkeypatch.setenv("MBG_BROKER_HOST", "mqtt-mt.example")
    monkeypatch.setenv("MBG_BROKER_PORT", "1884")
    monkeypatch.setenv("MBG_ALIVE_TIMEOUT", "9")
    monkeypatch.setenv("MBG_DB_PATH", "/data/x.db")
    monkeypatch.setenv("MBG_MONITOR_INTERVAL", "42")
    monkeypatch.setenv("MBG_DUMP_DIR", "/data/csv")

    rc = main([])  # exactement ce que fait `ExecStart=python -m mbg`
    assert rc == 0

    cfg = FakeSupervisor.last.config
    assert cfg.ble_address == "F9:98:08:73:85:AE"
    assert cfg.broker_host == "mqtt-mt.example"
    assert cfg.broker_port == 1884
    # tuning + monitoring propagés depuis l'ENV (via dataclasses.replace)
    assert cfg.alive_timeout == 9.0
    assert cfg.db_path == "/data/x.db"
    assert cfg.monitor_interval == 42.0
    assert cfg.dump_dir == "/data/csv"


def test_cli_args_override_env(captured_signals, monkeypatch):
    monkeypatch.setenv("MBG_BROKER_HOST", "from-env")
    rc = main(["--broker", "from-cli"])
    assert rc == 0
    assert FakeSupervisor.last.config.broker_host == "from-cli"


def test_api_token_propagated_from_env(captured_signals, monkeypatch):
    monkeypatch.setenv("MBG_API_TOKEN", "sekret")
    rc = main([])
    assert rc == 0
    assert FakeSupervisor.last.config.api_token == "sekret"


def test_main_ble_supervision_enabled(captured_signals, monkeypatch):
    monkeypatch.setenv("MBG_BLE_SUPERVISION_TIMEOUT_MS", "6000")
    rc = main([])
    assert rc == 0
    assert FakeSupervisor.last.config.ble_supervision_timeout_ms == 6000  # propagé au superviseur


def test_build_serve_none_without_token():
    assert _build_serve(Config(), None) is None


def test_build_serve_calls_api(monkeypatch):
    called = {}
    monkeypatch.setattr(main_mod.api, "serve", lambda *a: called.setdefault("args", a))
    serve = _build_serve(
        Config(api_token="t", api_host="h", api_port=9, control_timeout=3, monitor_interval=300), "METRICS"
    )
    serve("SUBMIT", "SHOULD_RUN")
    args = called["args"]
    assert args[:7] == ("h", 9, "t", 3, "SUBMIT", "METRICS", "SHOULD_RUN")
    assert args[7]["monitor_interval"] == 300 and args[7]["battery_tiers"] is False and args[7]["version"]


def test_main_no_store_when_monitoring_off(captured_signals, monkeypatch):
    monkeypatch.setenv("MBG_MONITOR_INTERVAL", "0")
    rc = main([])
    assert rc == 0  # store None (branche monitoring off)


def test_battery_tiers_enabled(captured_signals, monkeypatch):
    monkeypatch.setenv("MBG_BATTERY_TIERS", "true")
    monkeypatch.setenv("MBG_MONITOR_INTERVAL", "300")
    rc = main([])
    assert rc == 0
    assert FakeSupervisor.last.config.battery_tiers is True  # actif (monitoring présent)


def test_battery_tiers_disabled_without_monitoring(captured_signals, monkeypatch):
    monkeypatch.setenv("MBG_BATTERY_TIERS", "true")
    monkeypatch.setenv("MBG_MONITOR_INTERVAL", "0")  # pas de source batterie
    rc = main([])
    assert rc == 0
    assert FakeSupervisor.last.config.battery_tiers is False  # garde-fou : désactivé
