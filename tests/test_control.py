# SPDX-License-Identifier: AGPL-3.0-or-later
from types import SimpleNamespace

from mbg.control import execute_command


def _channel(index, name):
    return SimpleNamespace(index=index, settings=SimpleNamespace(name=name))


class FakeIface:
    def __init__(self):
        self.sent_text = None
        self.want_ack = False
        self.on_response = None
        self.telemetry = 0
        self.written = []
        self.localNode = SimpleNamespace(
            channels=[_channel(0, "Fr_Balise"), _channel(3, "meteo")],
            localConfig=SimpleNamespace(
                device=SimpleNamespace(role=0),
                position=SimpleNamespace(position_broadcast_secs=0, gps_mode=0),
            ),
            moduleConfig=SimpleNamespace(
                telemetry=SimpleNamespace(device_update_interval=0),
            ),
            writeConfig=self._write,
        )

    def _write(self, section):
        self.written.append(section)

    def sendText(self, text, channelIndex=0, destinationId=None, wantAck=False, onResponse=None):
        self.sent_text = (text, channelIndex, destinationId)
        self.want_ack = wantAck
        self.on_response = onResponse

    def sendTelemetry(self):
        self.telemetry += 1


def test_text_on_named_channel():
    iface = FakeIface()
    r = execute_command(iface, {"type": "text", "text": "hello", "channel": "meteo"})
    assert r["ok"] is True
    assert iface.sent_text == ("hello", 3, None)


def test_text_channel_by_index_with_dest():
    iface = FakeIface()
    r = execute_command(iface, {"type": "text", "text": "hi", "channel": 1, "dest": "!abc"})
    assert r["ok"] and iface.sent_text == ("hi", 1, "!abc")


def test_text_channel_digit_string():
    iface = FakeIface()
    execute_command(iface, {"type": "text", "text": "x", "channel": "2"})
    assert iface.sent_text[1] == 2


def test_text_missing_text():
    r = execute_command(FakeIface(), {"type": "text"})
    assert r["ok"] is False and "texte" in r["error"]


def test_text_unknown_channel():
    r = execute_command(FakeIface(), {"type": "text", "text": "x", "channel": "nope"})
    assert r["ok"] is False and "canal inconnu" in r["error"]


def test_text_without_want_ack():
    iface = FakeIface()
    execute_command(iface, {"type": "text", "text": "hi", "channel": 0})
    assert iface.want_ack is False and iface.on_response is None


def test_text_want_ack_registers_callback():
    iface = FakeIface()
    r = execute_command(iface, {"type": "text", "text": "hi", "channel": "meteo", "want_ack": True})
    assert r["ok"] and r["want_ack"] is True
    assert iface.want_ack is True and callable(iface.on_response)
    # invoquer le callback ACK (asynchrone en vrai) ne doit pas lever
    iface.on_response({"decoded": {"routing": {"errorReason": "NONE"}}})


def test_ack_status_ack():
    from mbg.control import _ack_status

    assert _ack_status({"decoded": {"routing": {"errorReason": "NONE"}}}) == "reçu (ACK)"


def test_ack_status_missing_routing_is_ack():
    from mbg.control import _ack_status

    assert _ack_status({}) == "reçu (ACK)"


def test_ack_status_nak():
    from mbg.control import _ack_status

    s = _ack_status({"decoded": {"routing": {"errorReason": "MAX_RETRANSMIT"}}})
    assert "échec" in s and "MAX_RETRANSMIT" in s


def test_telemetry():
    iface = FakeIface()
    r = execute_command(iface, {"type": "telemetry"})
    assert r["ok"] and iface.telemetry == 1


def test_admin_int_setting():
    iface = FakeIface()
    r = execute_command(iface, {"type": "admin", "setting": "position_broadcast_secs", "value": 43200})
    assert r["ok"]
    assert iface.localNode.localConfig.position.position_broadcast_secs == 43200
    assert "position" in iface.written


def test_admin_module_setting_coerces_string():
    iface = FakeIface()
    r = execute_command(iface, {"type": "admin", "setting": "device_update_interval", "value": "3600"})
    assert r["ok"]
    assert iface.localNode.moduleConfig.telemetry.device_update_interval == 3600
    assert "telemetry" in iface.written


def test_admin_role_by_name():
    iface = FakeIface()
    r = execute_command(iface, {"type": "admin", "setting": "role", "value": "ROUTER"})
    assert r["ok"]
    assert iface.localNode.localConfig.device.role == 2  # ROUTER
    assert "device" in iface.written


def test_admin_role_by_int():
    iface = FakeIface()
    execute_command(iface, {"type": "admin", "setting": "role", "value": 2})
    assert iface.localNode.localConfig.device.role == 2


def test_admin_gps_mode_by_name():
    iface = FakeIface()
    r = execute_command(iface, {"type": "admin", "setting": "gps_mode", "value": "DISABLED"})
    assert r["ok"]
    assert "position" in iface.written


def test_admin_gps_mode_by_int():
    iface = FakeIface()
    execute_command(iface, {"type": "admin", "setting": "gps_mode", "value": 0})
    assert iface.localNode.localConfig.position.gps_mode == 0


def test_admin_unknown_setting():
    r = execute_command(FakeIface(), {"type": "admin", "setting": "nope", "value": 1})
    assert r["ok"] is False and "inconnu" in r["error"]


def test_unknown_command_type():
    r = execute_command(FakeIface(), {"type": "zzz"})
    assert r["ok"] is False and "inconnu" in r["error"]


def test_exception_is_swallowed():
    class Boom:
        def sendTelemetry(self):
            raise RuntimeError("ble dead")

    r = execute_command(Boom(), {"type": "telemetry"})
    assert r["ok"] is False and "ble dead" in r["error"]
