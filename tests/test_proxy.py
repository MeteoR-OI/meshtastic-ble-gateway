# SPDX-License-Identifier: AGPL-3.0-or-later
from fakes import FakeProxyMessage, FakePublisher
from mbg.proxy import Proxy


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
