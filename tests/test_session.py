# SPDX-License-Identifier: AGPL-3.0-or-later
from fakes import FakeNodeLink, FakeProxyMessage, FakePublisher
from mbg.config import Config
from mbg.session import run_one_session


def seq(values):
    it = iter(values)
    return lambda: next(it)


def test_polls_and_heartbeats_until_silent_drop():
    pub = FakePublisher()
    box = {}

    def nf(addr, cb, on_lost):
        box["link"] = FakeNodeLink(addr, cb)
        return box["link"]

    beats = []
    slept = []

    def sleep_(s):
        slept.append(s)
        box["link"].alive = False  # décrochage silencieux après le 1er poll

    n = run_one_session(
        Config(poll_interval=0.5), lambda: pub, nf,
        lambda: beats.append(1), seq([True, True]), sleep=sleep_,
    )
    assert pub.connected
    assert box["link"].opened
    assert beats == [1]  # heartbeat émis à chaque poll
    assert slept == [0.5]
    assert n == 0


def test_lost_event_breaks():
    box = {}

    def nf(addr, cb, on_lost):
        box["on_lost"] = on_lost
        box["link"] = FakeNodeLink(addr, cb)
        return box["link"]

    def sleep_(s):
        box["on_lost"]()  # meshtastic signale la perte

    n = run_one_session(
        Config(poll_interval=0.5), lambda: FakePublisher(), nf,
        lambda: None, seq([True, True]), sleep=sleep_,
    )
    assert n == 0


def test_forwards_messages():
    pub = FakePublisher()
    box = {}

    def nf(addr, cb, on_lost):
        box["cb"] = cb
        box["link"] = FakeNodeLink(addr, cb)
        return box["link"]

    def sleep_(s):
        box["cb"](FakeProxyMessage())  # 1 message relayé pendant le poll
        box["link"].alive = False

    n = run_one_session(
        Config(poll_interval=0.5), lambda: pub, nf,
        lambda: None, seq([True, True]), sleep=sleep_,
    )
    assert n == 1
    assert len(pub.published) == 1


def test_stops_when_should_continue_false():
    pub = FakePublisher()
    box = {}

    def nf(addr, cb, on_lost):
        box["link"] = FakeNodeLink(addr, cb)
        return box["link"]

    n = run_one_session(Config(), lambda: pub, nf, lambda: None, lambda: False)
    assert pub.connected and box["link"].opened and n == 0
