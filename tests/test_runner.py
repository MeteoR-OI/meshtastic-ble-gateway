# SPDX-License-Identifier: AGPL-3.0-or-later
from fakes import FakeNodeLink, FakeProxyMessage, FakePublisher
from mbg.config import Config
from mbg.runner import Gateway


def seq(values):
    it = iter(values)
    return lambda: next(it)


def test_no_session_when_stopped_immediately():
    called = {"pub": 0, "link": 0}
    notes = []

    def pf():
        called["pub"] += 1
        return FakePublisher()

    def nf(a, cb, on_lost):
        called["link"] += 1
        return FakeNodeLink(a, cb)

    Gateway(Config(), pf, nf, sleep=lambda s: None, notify=notes.append).run(lambda: False)
    assert called == {"pub": 0, "link": 0}
    assert notes == ["READY=1"]  # prêt signalé, puis rien (arrêt immédiat)


def test_normal_session_then_clean_stop():
    pub = FakePublisher()
    box = {}

    def nf(addr, cb, on_lost):
        box["link"] = FakeNodeLink(addr, cb)
        return box["link"]

    slept = []
    notes = []
    gw = Gateway(Config(poll_interval=0.5), lambda: pub, nf, sleep=slept.append, notify=notes.append)
    gw.run(seq([True, True, False, False]))
    assert pub.connected and pub.closed
    assert box["link"].opened and box["link"].closed
    assert "READY=1" in notes and "WATCHDOG=1" in notes
    assert slept == [0.5]  # un poll, PAS de reconnexion (arrêt propre)


def test_silent_drop_detected_by_liveness_probe():
    pub = FakePublisher()
    box = {}

    def nf(addr, cb, on_lost):
        box["link"] = FakeNodeLink(addr, cb)
        return box["link"]

    slept = []

    def sleep_(s):
        slept.append(s)
        if s == 0.5:  # après le 1er poll : le lien meurt EN SILENCE
            box["link"].alive = False

    gw = Gateway(
        Config(poll_interval=0.5, reconnect_delay=5),
        lambda: pub, nf, sleep=sleep_, notify=lambda _: None,
    )
    gw.run(seq([True, True, True, True, False]))
    assert box["link"].closed
    assert slept == [0.5, 5]  # poll, puis backoff de reconnexion après détection


def test_connection_lost_event_triggers_reconnect():
    box = {}

    def nf(addr, cb, on_lost):
        box["on_lost"] = on_lost  # = lost.set
        box["link"] = FakeNodeLink(addr, cb)
        return box["link"]

    slept = []

    def sleep_(s):
        slept.append(s)
        if s == 0.5:
            box["on_lost"]()  # meshtastic signale la perte

    gw = Gateway(
        Config(poll_interval=0.5, reconnect_delay=5),
        lambda: FakePublisher(), nf, sleep=sleep_, notify=lambda _: None,
    )
    gw.run(seq([True, True, True, True, False]))
    assert slept == [0.5, 5]


def test_setup_failure_backoff_grows_and_caps():
    class BoomPublisher(FakePublisher):
        def connect(self):
            raise RuntimeError("no broker")

    slept = []
    gw = Gateway(
        Config(reconnect_delay=5, max_reconnect_delay=30),
        lambda: BoomPublisher(),
        lambda a, cb, ol: FakeNodeLink(a, cb),
        sleep=slept.append,
        notify=lambda _: None,
    )
    gw.run(seq([True, True, True, True, True, False]))
    assert slept == [5, 10, 20, 30, 30]  # exponentiel plafonné à 30


def test_productive_session_resets_backoff():
    # 1) échec setup -> delay 5->10 ; 2) session qui relaie 1 msg puis décroche
    #    -> forwarded>0 -> backoff remis à 5 (et non 10/20).
    state = {"n": 0}

    def pf():
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("broker down")
        return FakePublisher()

    box = {}

    def nf(addr, cb, on_lost):
        box["link"] = FakeNodeLink(addr, cb)
        box["cb"] = cb
        return box["link"]

    slept = []

    def sleep_(s):
        slept.append(s)
        if s == 0.5:  # session productive : 1 message relayé, puis drop
            box["cb"](FakeProxyMessage())
            box["link"].alive = False

    gw = Gateway(
        Config(poll_interval=0.5, reconnect_delay=5, max_reconnect_delay=30),
        pf, nf, sleep=sleep_, notify=lambda _: None,
    )
    gw.run(seq([True, True, True, True, True, False]))
    assert slept == [5, 0.5, 5]  # reconnect après échec (5), poll (0.5), reconnect RESET (5)
