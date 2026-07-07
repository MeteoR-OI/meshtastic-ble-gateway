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

    def nf(a, cb, on_lost):
        called["link"] += 1
        return FakeNodeLink(a, cb)

    Gateway(Config(), pf, nf, sleep=lambda s: None).run(lambda: False)
    assert called == {"pub": 0, "link": 0}


def test_normal_session_opens_and_closes():
    pub = FakePublisher()
    box = {}

    def nf(addr, cb, on_lost):
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
        lambda a, cb, ol: FakeNodeLink(a, cb),
        sleep=slept.append,
    )
    gw.run(seq_should_continue([True, False]))
    assert slept == [7]


def test_reconnect_on_ble_connection_lost():
    holder = {}

    def nf(addr, cb, on_lost):
        holder["on_lost"] = on_lost  # = lost.set de la session
        return FakeNodeLink(addr, cb)

    slept = []

    def sleep_(s):
        slept.append(s)
        # simuler une perte BLE pendant le 1er poll de la session
        if s == 0.5 and "fired" not in holder:
            holder["fired"] = True
            holder["on_lost"]()

    gw = Gateway(Config(poll_interval=0.5, reconnect_delay=3), lambda: FakePublisher(), nf, sleep=sleep_)
    gw.run(seq_should_continue([True, True, True, False]))
    # 0.5 = poll ; 3 = backoff de reconnexion déclenché par la perte
    assert 0.5 in slept
    assert 3 in slept
