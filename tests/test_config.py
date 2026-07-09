# SPDX-License-Identifier: AGPL-3.0-or-later
from mbg.config import DEFAULT_BLE, Config

ENV_KEYS = [
    "MBG_BLE_ADDRESS",
    "MBG_BROKER_HOST",
    "MBG_BROKER_PORT",
    "MBG_BROKER_USERNAME",
    "MBG_BROKER_PASSWORD",
    "MBG_RECONNECT_DELAY",
    "MBG_MAX_RECONNECT_DELAY",
    "MBG_POLL_INTERVAL",
    "MBG_SUPERVISOR_TICK",
    "MBG_CONNECT_GRACE",
    "MBG_ALIVE_TIMEOUT",
    "MBG_API_TOKEN",
    "MBG_API_HOST",
    "MBG_API_PORT",
    "MBG_CONTROL_TIMEOUT",
    "MBG_DB_PATH",
    "MBG_MONITOR_INTERVAL",
    "MBG_MONITOR_FORCE_TELEMETRY",
    "MBG_DUMP_DIR",
    "MBG_DUMP_INTERVAL",
    "MBG_RETENTION_DAYS",
    "MBG_BATTERY_TIERS",
    "MBG_DUTY_ON",
    "MBG_DUTY_OFF",
    "MBG_TIER_HYSTERESIS",
    "MBG_BLE_SUPERVISION_TIMEOUT_MS",
]


def test_defaults_from_empty_env():
    c = Config.from_env({})
    assert c.ble_address == DEFAULT_BLE
    assert c.broker_host == "localhost"
    assert c.broker_port == 1883
    assert c.broker_username is None
    assert c.broker_password is None
    assert c.reconnect_delay == 5.0
    assert c.max_reconnect_delay == 30.0
    assert c.poll_interval == 0.5
    assert c.supervisor_tick == 1.0
    assert c.connect_grace == 45.0
    assert c.alive_timeout == 15.0
    assert c.api_token is None
    assert c.api_host == "0.0.0.0"
    assert c.api_port == 8080
    assert c.control_timeout == 10.0
    assert c.db_path == "metrics.db"
    assert c.monitor_interval == 300.0
    assert c.force_telemetry is False
    assert c.dump_dir is None
    assert c.dump_interval == 3600.0
    assert c.retention_days == 0.0
    assert c.battery_tiers is False
    assert c.duty_on == 300.0
    assert c.duty_off == 1800.0
    assert c.tier_hysteresis == 3.0
    assert c.ble_supervision_timeout_ms == 0


def test_full_env_override():
    c = Config.from_env(
        {
            "MBG_BLE_ADDRESS": "AA:BB:CC",
            "MBG_BROKER_HOST": "broker.local",
            "MBG_BROKER_PORT": "1884",
            "MBG_BROKER_USERNAME": "u",
            "MBG_BROKER_PASSWORD": "p",
            "MBG_RECONNECT_DELAY": "10",
            "MBG_MAX_RECONNECT_DELAY": "45",
            "MBG_POLL_INTERVAL": "1.5",
            "MBG_SUPERVISOR_TICK": "2",
            "MBG_CONNECT_GRACE": "60",
            "MBG_ALIVE_TIMEOUT": "12",
            "MBG_API_TOKEN": "tok",
            "MBG_API_HOST": "127.0.0.1",
            "MBG_API_PORT": "9090",
            "MBG_CONTROL_TIMEOUT": "7",
            "MBG_DB_PATH": "/data/m.db",
            "MBG_MONITOR_INTERVAL": "60",
            "MBG_MONITOR_FORCE_TELEMETRY": "true",
            "MBG_DUMP_DIR": "/data/csv",
            "MBG_DUMP_INTERVAL": "1800",
            "MBG_RETENTION_DAYS": "30",
            "MBG_BATTERY_TIERS": "true",
            "MBG_DUTY_ON": "120",
            "MBG_DUTY_OFF": "900",
            "MBG_TIER_HYSTERESIS": "5",
            "MBG_BLE_SUPERVISION_TIMEOUT_MS": "6000",
        }
    )
    assert c.ble_address == "AA:BB:CC"
    assert c.broker_host == "broker.local"
    assert c.broker_port == 1884
    assert c.broker_username == "u"
    assert c.broker_password == "p"
    assert c.reconnect_delay == 10.0
    assert c.max_reconnect_delay == 45.0
    assert c.poll_interval == 1.5
    assert c.supervisor_tick == 2.0
    assert c.connect_grace == 60.0
    assert c.alive_timeout == 12.0
    assert c.api_token == "tok"
    assert c.api_host == "127.0.0.1"
    assert c.api_port == 9090
    assert c.control_timeout == 7.0
    assert c.db_path == "/data/m.db"
    assert c.monitor_interval == 60.0
    assert c.force_telemetry is True
    assert c.dump_dir == "/data/csv"
    assert c.dump_interval == 1800.0
    assert c.retention_days == 30.0
    assert c.battery_tiers is True
    assert c.duty_on == 120.0
    assert c.duty_off == 900.0
    assert c.tier_hysteresis == 5.0
    assert c.ble_supervision_timeout_ms == 6000


def test_empty_credentials_become_none():
    c = Config.from_env({"MBG_BROKER_USERNAME": "", "MBG_BROKER_PASSWORD": ""})
    assert c.broker_username is None
    assert c.broker_password is None


def test_from_env_uses_os_environ_when_none(monkeypatch):
    for k in ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    c = Config.from_env()
    assert c.ble_address == DEFAULT_BLE
    assert c.broker_host == "localhost"


def test_direct_construction_defaults():
    assert Config().ble_address == DEFAULT_BLE
