# SPDX-License-Identifier: AGPL-3.0-or-later
"""Doublures de test (pas de matériel, pas de réseau)."""
from __future__ import annotations


class FakeProxyMessage:
    def __init__(self, topic="msh/EU_868/2/e/Fr_Balise/!534bbea5", data=b"abc"):
        self.topic = topic
        self.data = data


class FakePublisher:
    def __init__(self, raise_on_publish=False):
        self.published = []
        self.connected = False
        self.closed = False
        self._raise = raise_on_publish

    def connect(self):
        self.connected = True

    def publish(self, topic, payload):
        if self._raise:
            raise RuntimeError("broker indisponible")
        self.published.append((topic, payload))

    def close(self):
        self.closed = True


class FakeClient:
    """Imite l'API paho utilisée par PahoPublisher."""

    def __init__(self):
        self.calls = []
        self.username = None

    def username_pw_set(self, user, password):
        self.username = (user, password)

    def connect(self, host, port, keepalive=60):
        self.calls.append(("connect", host, port, keepalive))

    def loop_start(self):
        self.calls.append(("loop_start",))

    def publish(self, topic, payload):
        self.calls.append(("publish", topic, payload))

    def loop_stop(self):
        self.calls.append(("loop_stop",))

    def disconnect(self):
        self.calls.append(("disconnect",))


class FakeIface:
    def __init__(self, address=None):
        self.address = address
        self.closed = False

    def close(self):
        self.closed = True


class FakeNodeLink:
    def __init__(self, address, on_proxy):
        self.address = address
        self.on_proxy = on_proxy
        self.opened = False
        self.closed = False
        self.alive = True  # basculer à False pour simuler un décrochage BLE

    def open(self):
        self.opened = True

    def close(self):
        self.closed = True

    def is_alive(self):
        return self.alive
