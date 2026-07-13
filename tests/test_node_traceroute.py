# SPDX-License-Identifier: AGPL-3.0-or-later
from types import SimpleNamespace

from mbg.node import RECEIVE_TOPIC, MeshtasticNodeLink


class FakeTracerouteIface:
    def __init__(self, addr=None):
        self.sent_data = None
        self.nodesByNum = {
            2: {"user": {"id": "!aa"}},
            3: {},  # sans user.id -> fallback
        }
        self._info = {"num": 1, "user": {"id": "!gw"}}

    def sendData(self, data, destinationId=None, portNum=None, wantResponse=None,
                 channelIndex=None, hopLimit=None):
        self.sent_data = SimpleNamespace(
            data=data, dest=destinationId, portNum=portNum,
            wantResponse=wantResponse, channelIndex=channelIndex, hopLimit=hopLimit,
        )
        return SimpleNamespace(id=54321)

    def getMyNodeInfo(self):
        return self._info

    def close(self):
        self.closed = True


class FakeCoordinator:
    def __init__(self):
        self.started = None
        self.packets = []
        self.cancelled = False
        self._raise = None

    def start(self, dest, **kw):
        if self._raise:
            raise self._raise
        self.started = (dest, kw)
        return {"ok": True, "request_id": 54321, "dest": dest}

    def on_packet(self, packet):
        self.packets.append(packet)

    def cancel_all(self):
        self.cancelled = True


def _link(iface=None, subs=None):
    iface = iface or FakeTracerouteIface()
    link = MeshtasticNodeLink(
        "addr", lambda m: None,
        interface_factory=lambda a: iface,
        subscribe=lambda h, t: (subs.setdefault(t, h) if subs is not None else None),
        unsubscribe=lambda h, t: None,
    )
    link.open()
    return link, iface


def test_send_traceroute_not_activated():
    link, _ = _link()
    assert link.send({"type": "traceroute", "dest": "!aa"}) == {"ok": False, "error": "traceroute non activé"}


def test_send_traceroute_delegates_to_coordinator():
    link, _ = _link()
    coord = FakeCoordinator()
    link.attach_traceroute(coord)
    res = link.send({"type": "traceroute", "dest": "!aa", "hop_limit": 5, "channel_index": 2,
                     "timeout_s": 20.0, "source": "api"})
    assert res["ok"] is True and res["request_id"] == 54321
    dest, kw = coord.started
    assert dest == "!aa" and kw == {"hop_limit": 5, "channel_index": 2, "timeout_s": 20.0, "source": "api"}


def test_send_traceroute_defaults():
    link, _ = _link()
    coord = FakeCoordinator()
    link.attach_traceroute(coord)
    link.send({"type": "traceroute", "dest": "!aa"})
    _, kw = coord.started
    assert kw == {"hop_limit": 7, "channel_index": 0, "timeout_s": 30.0, "source": "api"}


def test_send_traceroute_value_error():
    link, _ = _link()
    coord = FakeCoordinator()
    coord._raise = ValueError("dest invalide")
    link.attach_traceroute(coord)
    res = link.send({"type": "traceroute", "dest": "bad"})
    assert res == {"ok": False, "error": "dest invalide"}


def test_send_traceroute_low_level():
    link, iface = _link()
    from meshtastic.protobuf import portnums_pb2

    pid = link.send_traceroute(0x6984DDB0, 6, 1)
    assert pid == 54321
    assert iface.sent_data.dest == 0x6984DDB0
    assert iface.sent_data.portNum == portnums_pb2.PortNum.TRACEROUTE_APP
    assert iface.sent_data.wantResponse is True
    assert iface.sent_data.hopLimit == 6 and iface.sent_data.channelIndex == 1


def test_node_helpers():
    link, _ = _link()
    assert link.nodes()[2] == {"user": {"id": "!aa"}}
    assert link.my_num() == 1
    assert link.node_id_of(2) == "!aa"
    assert link.node_id_of(3) == "!00000003"    # pas de user.id -> fallback
    assert link.node_id_of(9) == "!00000009"    # num absent -> fallback
    assert link.gateway_id() == "!gw"


def test_gateway_id_fallback_num():
    iface = FakeTracerouteIface()
    iface._info = {"num": 0x362E105B}  # pas de user.id -> !hex du num
    link, _ = _link(iface=iface)
    assert link.gateway_id() == "!362e105b"


def test_gateway_id_none():
    iface = FakeTracerouteIface()
    iface._info = {}  # ni user ni num
    link, _ = _link(iface=iface)
    assert link.gateway_id() is None


def test_receive_feeds_coordinator():
    subs = {}
    link, _ = _link(subs=subs)
    coord = FakeCoordinator()
    link.attach_traceroute(coord)
    packet = {"decoded": {"portnum": "TRACEROUTE_APP", "requestId": 1}}
    subs[RECEIVE_TOPIC](packet=packet, interface="i")
    assert coord.packets == [packet]


def test_receive_no_coordinator():
    subs = {}
    link, _ = _link(subs=subs)
    # aucun coordinateur : ne plante pas (branche self._traceroute is None)
    subs[RECEIVE_TOPIC](packet={"decoded": {"portnum": "TEXT_MESSAGE_APP"}}, interface="i")


def test_close_cancels_coordinator():
    link, iface = _link()
    coord = FakeCoordinator()
    link.attach_traceroute(coord)
    link.close()
    assert coord.cancelled is True
