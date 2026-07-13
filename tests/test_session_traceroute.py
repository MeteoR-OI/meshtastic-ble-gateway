# SPDX-License-Identifier: AGPL-3.0-or-later
from fakes import FakeNodeLink, FakePublisher
from mbg.config import Config
from mbg.session import run_one_session


def seq(values):
    it = iter(values)
    return lambda: next(it)


class FakeCoordinator:
    def __init__(self):
        self.starts = []

    def start(self, dest, **kw):
        self.starts.append((dest, kw))


class FakeScheduler:
    def __init__(self, commands):
        self._commands = list(commands)

    def poll(self):
        return self._commands.pop(0) if self._commands else None


def test_traceroute_setup_attaches_and_schedules():
    pub = FakePublisher()
    box = {}
    coord = FakeCoordinator()
    cmd = {"dest": "!aa", "hop_limit": 7, "channel_index": 0, "timeout_s": 30.0, "source": "scheduler:static"}
    sched = FakeScheduler([cmd, None])

    def nf(addr, cb, on_lost):
        box["link"] = FakeNodeLink(addr, cb)
        return box["link"]

    setup_calls = []

    def setup(link, publisher):
        setup_calls.append((link, publisher))
        return coord, sched

    def sleep_(s):
        if box["link"].alive:
            box["link"].alive = False  # 2e poll -> drop

    run_one_session(
        Config(poll_interval=0.5), lambda: pub, nf, lambda: None, seq([True, True, True]),
        sleep=sleep_, traceroute_setup=setup,
    )
    # coordinateur attaché au link
    assert box["link"].traceroute is coord
    assert setup_calls[0][1] is pub
    # 1er poll : scheduler.poll -> cmd -> coordinator.start
    assert coord.starts == [("!aa", {"hop_limit": 7, "channel_index": 0, "timeout_s": 30.0, "source": "scheduler:static"})]


def test_scheduler_poll_returns_none():
    # scheduler présent mais poll() -> None : branche "pas d'émission" (auto is None)
    pub = FakePublisher()
    box = {}
    coord = FakeCoordinator()
    sched = FakeScheduler([None])

    def nf(addr, cb, on_lost):
        box["link"] = FakeNodeLink(addr, cb)
        return box["link"]

    def sleep_(s):
        box["link"].alive = False

    run_one_session(
        Config(poll_interval=0.5), lambda: pub, nf, lambda: None, seq([True, True]),
        sleep=sleep_, traceroute_setup=lambda link, publisher: (coord, sched),
    )
    assert coord.starts == []  # poll a renvoyé None -> aucune émission


def test_traceroute_setup_without_scheduler():
    # setup renvoie un coordinateur mais pas de scheduler (endpoint seul) -> pas de poll auto
    pub = FakePublisher()
    box = {}
    coord = FakeCoordinator()

    def nf(addr, cb, on_lost):
        box["link"] = FakeNodeLink(addr, cb)
        return box["link"]

    def sleep_(s):
        box["link"].alive = False

    run_one_session(
        Config(poll_interval=0.5), lambda: pub, nf, lambda: None, seq([True, True]),
        sleep=sleep_, traceroute_setup=lambda link, publisher: (coord, None),
    )
    assert box["link"].traceroute is coord
    assert coord.starts == []
