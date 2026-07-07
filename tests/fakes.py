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


class _DummyLock:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeCounter:
    """Imite multiprocessing.Value (get_lock + value)."""

    def __init__(self):
        self.value = 0

    def get_lock(self):
        return _DummyLock()


class FakeWorkerHandle:
    """Handle worker pilotable pour tester le superviseur (aucun vrai process)."""

    def __init__(self, beat_value=0, alive=True):
        self.beat_value = beat_value
        self.alive = alive
        self.killed = False
        self.joined = False

    def beats(self):
        return self.beat_value

    def is_alive(self):
        return self.alive

    def kill(self):
        self.killed = True
        self.alive = False

    def join(self, timeout=None):
        self.joined = True


class FakeProcess:
    """Imite multiprocessing.Process pour tester process_backend sans fork."""

    def __init__(self, target=None, args=(), name=None, daemon=None):
        self.target = target
        self.args = args
        self.name = name
        self.daemon = daemon
        self.started = False
        self.killed = False
        self.joined_with = "unset"
        self.exitcode = 0
        self._alive = False

    def start(self):
        self.started = True
        self._alive = True

    def is_alive(self):
        return self._alive

    def kill(self):
        self.killed = True
        self._alive = False

    def join(self, timeout=None):
        self.joined_with = timeout


class FakeContext:
    """Imite un contexte multiprocessing (Value + Process)."""

    def __init__(self):
        self.last_process = None

    def Value(self, typecode, initial):
        c = FakeCounter()
        c.value = initial
        return c

    def Process(self, target=None, args=(), name=None, daemon=None):
        self.last_process = FakeProcess(target=target, args=args, name=name, daemon=daemon)
        return self.last_process
