# SPDX-License-Identifier: AGPL-3.0-or-later
from types import SimpleNamespace

from mbg.control import execute_command


def _channel(index, name):
    return SimpleNamespace(index=index, settings=SimpleNamespace(name=name))


class FakeIface:
    def __init__(self, node_position=None):
        self.sent_text = None
        self.want_ack = False
        self.telemetry = 0
        self.sent_position = None
        self._node_position = node_position if node_position is not None else {
            "latitude": -21.34, "longitude": 55.47, "altitude": 120,
        }
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

    def sendText(self, text, channelIndex=0, destinationId=None, wantAck=False):
        self.sent_text = (text, channelIndex, destinationId)
        self.want_ack = wantAck
        return SimpleNamespace(id=999)  # paquet meshtastic (avec son id)

    def sendTelemetry(self):
        self.telemetry += 1

    def getMyNodeInfo(self):
        return {"position": self._node_position}

    def sendPosition(self, latitude, longitude, channelIndex=0, altitude=None):
        self.sent_position = (latitude, longitude, channelIndex, altitude)
        return SimpleNamespace(id=1234)


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
    r = execute_command(iface, {"type": "text", "text": "hi", "channel": 0})
    assert iface.want_ack is False
    assert "packet_id" not in r  # pas de suivi d'ACK


def test_text_want_ack_returns_packet_id():
    iface = FakeIface()
    r = execute_command(iface, {"type": "text", "text": "hi", "channel": "meteo", "want_ack": True})
    assert r["ok"] and r["want_ack"] is True
    assert iface.want_ack is True
    assert r["packet_id"] == 999  # id du paquet, pour corrélation ACK côté node


def test_telemetry():
    iface = FakeIface()
    r = execute_command(iface, {"type": "telemetry"})
    assert r["ok"] and iface.telemetry == 1


def test_position_reemits_node_fixed_position():
    iface = FakeIface()
    r = execute_command(iface, {"type": "position"})
    assert r["ok"] and r["id"] == 1234
    # ré-émet la position FIXE lue sur le node (jamais 0,0), altitude comprise, canal 0
    assert iface.sent_position == (-21.34, 55.47, 0, 120)


def test_position_explicit_override():
    iface = FakeIface()
    r = execute_command(iface, {"type": "position", "lat": 1.5, "lon": 2.5, "alt": 10})
    assert r["ok"]
    assert iface.sent_position == (1.5, 2.5, 0, 10)  # override sans lire le node


def test_position_partial_override_reads_node_for_missing():
    iface = FakeIface()
    execute_command(iface, {"type": "position", "lat": 9.0})  # lon manquant -> lu sur le node
    assert iface.sent_position == (9.0, 55.47, 0, 120)


def test_position_partial_override_keeps_explicit_alt():
    iface = FakeIface()
    execute_command(iface, {"type": "position", "lon": 2.5, "alt": 50})  # lat lu, alt gardée
    assert iface.sent_position == (-21.34, 2.5, 0, 50)


def test_position_no_alt_omits_altitude():
    iface = FakeIface(node_position={"latitude": 1.0, "longitude": 2.0})  # pas d'altitude
    execute_command(iface, {"type": "position"})
    assert iface.sent_position == (1.0, 2.0, 0, None)


def test_position_refuses_when_unknown():
    iface = FakeIface(node_position={})  # ni payload ni position node
    r = execute_command(iface, {"type": "position"})
    assert r["ok"] is False and "refus d'émettre 0,0" in r["error"]
    assert iface.sent_position is None  # rien émis


def test_position_node_info_none_refuses():
    class NoInfo(FakeIface):
        def getMyNodeInfo(self):
            return None  # branche `getMyNodeInfo() or {}`

    r = execute_command(NoInfo(), {"type": "position"})
    assert r["ok"] is False and "refus" in r["error"]


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
