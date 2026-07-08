# SPDX-License-Identifier: AGPL-3.0-or-later
from fakes import FakeProxyMessage, FakePublisher
from mbg.proxy import Proxy, _envelope_header


def _envelope_bytes(from_num, packet_id):
    from meshtastic.protobuf import mqtt_pb2

    env = mqtt_pb2.ServiceEnvelope()
    env.packet.id = packet_id
    setattr(env.packet, "from", from_num)  # 'from' est un mot-clé Python -> setattr
    return env.SerializeToString()


def test_envelope_header_decodes():
    src, pid = _envelope_header(_envelope_bytes(0x592AEF4C, 12345))
    assert src == "!592aef4c" and pid == 12345


def test_envelope_header_bad_bytes_returns_none():
    assert _envelope_header(b"\xff\xff\xff\xff") == (None, None)


def test_uplink_log_includes_header():
    pub = FakePublisher()
    proxy = Proxy(pub)
    data = _envelope_bytes(0x1, 7)
    proxy.on_proxy_message(FakeProxyMessage("msh/x", data))
    assert proxy.forwarded == 1  # branche log enrichie (src non None)
    assert pub.published[0][1] == data  # forward toujours opaque (inchangé)


def test_forwards_message_verbatim():
    pub = FakePublisher()
    proxy = Proxy(pub)
    proxy.on_proxy_message(FakeProxyMessage("topic/x", b"payload"))
    assert pub.published == [("topic/x", b"payload")]
    assert proxy.forwarded == 1
    assert proxy.errors == 0


def test_publish_error_is_swallowed():
    pub = FakePublisher(raise_on_publish=True)
    proxy = Proxy(pub)
    proxy.on_proxy_message(FakeProxyMessage())
    assert proxy.forwarded == 0
    assert proxy.errors == 1
