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
