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

    def fake_session(config, publisher_factory, nodelink_factory, heartbeat, should_continue, commands=None):
        captured["pub"] = publisher_factory()  # exerce publisher_factory
        captured["link"] = nodelink_factory("a", lambda m: None, lambda: None)  # et nodelink_factory
        captured["commands"] = commands
        heartbeat()
        heartbeat()
        return 3

    sentinel = object()
    rc = _worker_body(
        Config(broker_host="h"), counter, sentinel,
        session=fake_session, publisher_cls=FakePub, nodelink_cls=FakeLink,
    )
    assert rc == EXIT_RESPAWN
    assert counter.value == 2  # 2 heartbeats
    assert captured["pub"].args[0] == "h"
    assert captured["link"].args[0] == "a"
    assert captured["commands"] is sentinel  # canal de commandes transmis à la session


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

    rc = _worker_body(Config(), counter, session=boom, publisher_cls=FakePub, nodelink_cls=FakeLink)
    assert rc == EXIT_RESPAWN  # ne relève pas -> le parent respawn
