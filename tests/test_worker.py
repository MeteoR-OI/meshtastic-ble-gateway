# SPDX-License-Identifier: AGPL-3.0-or-later
import queue

from fakes import FakeCounter
from mbg.config import Config
from mbg.worker import EXIT_RESPAWN, QueueCommandChannel, _worker_body


class FakePub:
    def __init__(self, host, port, user, pw):
        self.args = (host, port, user, pw)


class FakeLink:
    def __init__(self, addr, on_proxy, on_lost):
        self.args = (addr, on_proxy, on_lost)


class _Q:
    def __init__(self, items=None):
        self.items = list(items or [])
        self.puts = []

    def get_nowait(self):
        if self.items:
            return self.items.pop(0)
        raise queue.Empty

    def put(self, item):
        self.puts.append(item)


def test_worker_body_runs_session_and_heartbeats():
    counter = FakeCounter()
    captured = {}

    def fake_session(config, publisher_factory, nodelink_factory, heartbeat, should_continue, commands=None, monitor=None, tune=None, traceroute_setup=None):
        captured["pub"] = publisher_factory()  # exerce publisher_factory
        captured["link"] = nodelink_factory("a", lambda m: None, lambda: None)  # et nodelink_factory
        captured["commands"] = commands
        captured["monitor"] = monitor
        captured["tune"] = tune
        heartbeat()
        heartbeat()
        return 3

    sentinel = object()
    rc = _worker_body(
        Config(broker_host="h", monitor_interval=0), counter, sentinel,
        session=fake_session, publisher_cls=FakePub, nodelink_cls=FakeLink,
    )
    assert rc == EXIT_RESPAWN
    assert counter.value == 2  # 2 heartbeats
    assert captured["pub"].args[0] == "h"
    assert captured["link"].args[0] == "a"
    assert captured["commands"] is sentinel  # canal de commandes transmis à la session
    assert captured["monitor"] is None  # monitoring désactivé (interval=0)
    assert captured["tune"] is None  # stabilisation BLE désactivée (timeout=0)


def test_worker_body_tunes_when_enabled():
    tuned = []
    captured = {}

    def fake_session(config, pf, nf, hb, sc, commands=None, monitor=None, tune=None, traceroute_setup=None):
        captured["tune"] = tune
        tune()  # la session déclenche le réglage une fois le lien établi

    _worker_body(
        Config(monitor_interval=0, ble_supervision_timeout_ms=6000), FakeCounter(),
        session=fake_session, publisher_cls=FakePub, nodelink_cls=FakeLink,
        tuner=lambda cfg: tuned.append(cfg) or True,
    )
    assert captured["tune"] is not None
    assert len(tuned) == 1  # tuner appelé avec la config
    assert tuned[0].ble_supervision_timeout_ms == 6000


class FakeStore:
    def __init__(self, path):
        self.path = path
        self.nodes = []
        self.neigh = []

    def record_node(self, m, p):
        self.nodes.append((m, p))

    def upsert_neighbors(self, n):
        self.neigh.append(n)


class MonLink:
    def __init__(self):
        self.sent = []
        self.read_kwargs = None

    def send(self, command):
        self.sent.append(command)

    def read_metrics(self, *, now=None, active_window=None):
        self.read_kwargs = {"now": now, "active_window": active_window}
        return {"node": {"battery_level": 80}, "position": {"lat": 1}, "neighbors": [{"node_id": "!x"}]}


def _session_calling_monitor(link):
    def session(config, pf, nf, hb, sc, commands=None, monitor=None, tune=None, traceroute_setup=None):
        monitor(link)

    return session


def test_worker_body_monitoring_records():
    stores = []
    link = MonLink()
    _worker_body(
        Config(monitor_interval=300, db_path="x.db"), FakeCounter(),
        session=_session_calling_monitor(link),
        publisher_cls=FakePub, nodelink_cls=FakeLink,
        store_cls=lambda p: stores.append(FakeStore(p)) or stores[-1],
        clock=lambda: 1000.0,
    )
    assert stores[0].nodes == [({"battery_level": 80}, {"lat": 1})]
    assert stores[0].neigh == [[{"node_id": "!x"}]]
    assert link.sent == []  # force_telemetry False
    # filtre "voisin actif" passé à l'extraction : now=horloge, fenêtre=max(monitor_interval, plancher 3600)
    assert link.read_kwargs == {"now": 1000.0, "active_window": 3600.0}


def test_worker_body_monitoring_force_telemetry():
    link = MonLink()
    _worker_body(
        Config(monitor_interval=300, force_telemetry=True, db_path="x.db"), FakeCounter(),
        session=_session_calling_monitor(link),
        publisher_cls=FakePub, nodelink_cls=FakeLink, store_cls=lambda p: FakeStore(p),
    )
    assert link.sent == [{"type": "telemetry"}]  # mesure forcée avant lecture


def test_worker_body_monitoring_off_no_store():
    calls = {"store": 0}

    def session(config, pf, nf, hb, sc, commands=None, monitor=None, tune=None, traceroute_setup=None):
        assert monitor is None

    _worker_body(
        Config(monitor_interval=0), FakeCounter(),
        session=session, publisher_cls=FakePub, nodelink_cls=FakeLink,
        store_cls=lambda p: calls.__setitem__("store", calls["store"] + 1),
    )
    assert calls["store"] == 0  # store non créé


def test_queue_command_channel_drain_and_reply():
    cmd_q = _Q([{"id": 1}, {"id": 2}])
    res_q = _Q()
    ch = QueueCommandChannel(cmd_q, res_q)
    assert ch.drain() == [{"id": 1}, {"id": 2}]
    assert ch.drain() == []  # queue vide
    ch.reply(7, {"ok": True})
    assert res_q.puts == [{"id": 7, "ok": True}]


def test_worker_body_swallows_session_error():
    counter = FakeCounter()

    def boom(*a, **k):
        raise RuntimeError("broker down")

    rc = _worker_body(
        Config(monitor_interval=0), counter, session=boom, publisher_cls=FakePub, nodelink_cls=FakeLink
    )
    assert rc == EXIT_RESPAWN  # ne relève pas -> le parent respawn
