# SPDX-License-Identifier: AGPL-3.0-or-later
from fakes import FakeCounter
from mbg.config import Config
from mbg.worker import EXIT_RESPAWN, _worker_body


class FakePub:
    def __init__(self, host, port, user, pw):
        self.args = (host, port, user, pw)


class FakeLink:
    def __init__(self, addr, on_proxy, on_lost):
        self.args = (addr, on_proxy, on_lost)


def test_worker_body_runs_session_and_heartbeats():
    counter = FakeCounter()
    captured = {}

    def fake_session(config, publisher_factory, nodelink_factory, heartbeat, should_continue):
        captured["pub"] = publisher_factory()  # exerce publisher_factory
        captured["link"] = nodelink_factory("a", lambda m: None, lambda: None)  # et nodelink_factory
        heartbeat()
        heartbeat()
        return 3

    rc = _worker_body(
        Config(broker_host="h"), counter,
        session=fake_session, publisher_cls=FakePub, nodelink_cls=FakeLink,
    )
    assert rc == EXIT_RESPAWN
    assert counter.value == 2  # 2 heartbeats
    assert captured["pub"].args[0] == "h"
    assert captured["link"].args[0] == "a"


def test_worker_body_swallows_session_error():
    counter = FakeCounter()

    def boom(*a, **k):
        raise RuntimeError("broker down")

    rc = _worker_body(Config(), counter, session=boom, publisher_cls=FakePub, nodelink_cls=FakeLink)
    assert rc == EXIT_RESPAWN  # ne relève pas -> le parent respawn
