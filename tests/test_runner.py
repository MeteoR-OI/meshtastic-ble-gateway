# SPDX-License-Identifier: AGPL-3.0-or-later
from fakes import FakeNodeLink, FakePublisher
from mbg.config import Config
from mbg.runner import Gateway


def seq_should_continue(values):
    it = iter(values)
    return lambda: next(it)


def test_no_session_when_stopped_immediately():
    called = {"pub": 0, "link": 0}

    def pf():
        called["pub"] += 1
        return FakePublisher()

    def nf(a, cb):
        called["link"] += 1
        return FakeNodeLink(a, cb)

    Gateway(Config(), pf, nf, sleep=lambda s: None).run(lambda: False)
    assert called == {"pub": 0, "link": 0}


def test_normal_session_opens_and_closes():
    pub = FakePublisher()
    box = {}

    def nf(addr, cb):
        box["link"] = FakeNodeLink(addr, cb)
        return box["link"]

    slept = []
    gw = Gateway(Config(poll_interval=0.5), lambda: pub, nf, sleep=slept.append)
    # run:True -> session ; inner:True(sleep),False(exit) ; run:False
    gw.run(seq_should_continue([True, True, False, False]))
    assert pub.connected and pub.closed
    assert box["link"].opened and box["link"].closed
    assert 0.5 in slept


def test_reconnect_delay_on_session_error():
    class BoomPublisher(FakePublisher):
        def connect(self):
            raise RuntimeError("no broker")

    slept = []
    gw = Gateway(
        Config(reconnect_delay=7),
        lambda: BoomPublisher(),
        lambda a, cb: FakeNodeLink(a, cb),
        sleep=slept.append,
    )
    gw.run(seq_should_continue([True, False]))
    assert slept == [7]
