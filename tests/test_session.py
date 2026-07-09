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


class FakeCommands:
    def __init__(self, batches):
        self.batches = list(batches)
        self.replies = []

    def drain(self):
        return self.batches.pop(0) if self.batches else []

    def reply(self, cmd_id, result):
        self.replies.append((cmd_id, result))


def test_command_pump_executes_and_replies():
    pub = FakePublisher()
    box = {}

    def nf(addr, cb, on_lost):
        box["link"] = FakeNodeLink(addr, cb)
        return box["link"]

    # 1er poll : une commande ; 2e poll : rien (drain vide) ; puis décrochage.
    cmds = FakeCommands([[{"id": 1, "type": "text"}], []])
    step = {"n": 0}

    def sleep_(s):
        step["n"] += 1
        if step["n"] == 2:
            box["link"].alive = False

    run_one_session(
        Config(poll_interval=0.5), lambda: pub, nf, lambda: None,
        seq([True, True, True]), sleep=sleep_, commands=cmds,
    )
    assert box["link"].sent == [{"id": 1, "type": "text"}]  # commande exécutée via link.send
    assert cmds.replies == [(1, {"ok": True})]  # résultat renvoyé


def test_monitor_called_on_cadence():
    pub = FakePublisher()
    box = {}

    def nf(addr, cb, on_lost):
        box["link"] = FakeNodeLink(addr, cb)
        return box["link"]

    monitored = []
    slept = []

    def sleep_(s):
        slept.append(s)
        if len(slept) >= 3:  # décrochage après 3 polls
            box["link"].alive = False

    # monitor_interval=1s, poll=0.5s -> polls_per_monitor=2 : relevé au 1er poll (tôt),
    # puis tous les 2 polls. Décrochage après 3 polls -> relevés aux polls 1 et 3.
    run_one_session(
        Config(poll_interval=0.5, monitor_interval=1.0), lambda: pub, nf, lambda: None,
        seq([True, True, True, True]), sleep=sleep_, monitor=lambda link: monitored.append(link),
    )
    assert len(monitored) == 2  # 1 tôt (poll 1) + 1 périodique (poll 3)
    assert monitored[0] is box["link"]


def test_monitor_sampled_before_first_periodic_tick():
    """Régression : session qui meurt AVANT le 1er tic périodique -> quand même 1 relevé.

    Cas terrain : lien instable, sessions ~267 s < monitor_interval=300 s. Sans relevé
    précoce, node_metrics resterait vide en permanence.
    """
    pub = FakePublisher()
    box = {}

    def nf(addr, cb, on_lost):
        box["link"] = FakeNodeLink(addr, cb)
        return box["link"]

    monitored = []

    def sleep_(s):
        box["link"].alive = False  # la session meurt dès le 1er poll (bien avant 300 s)

    run_one_session(
        Config(poll_interval=0.5, monitor_interval=300.0), lambda: pub, nf, lambda: None,
        seq([True, True]), sleep=sleep_, monitor=lambda link: monitored.append(link),
    )
    assert len(monitored) == 1  # relevé précoce garanti malgré la session courte


def test_tune_called_once_after_open():
    pub = FakePublisher()
    box = {}
    events = []

    def nf(addr, cb, on_lost):
        box["link"] = FakeNodeLink(addr, cb)
        box["link"].open = lambda: events.append("open")  # trace l'ordre open->tune
        return box["link"]

    run_one_session(
        Config(), lambda: pub, nf, lambda: None, lambda: False,
        tune=lambda: events.append("tune"),
    )
    assert events == ["open", "tune"]  # réglage exactement une fois, après l'établissement


def test_stops_when_should_continue_false():
    pub = FakePublisher()
    box = {}

    def nf(addr, cb, on_lost):
        box["link"] = FakeNodeLink(addr, cb)
        return box["link"]

    n = run_one_session(Config(), lambda: pub, nf, lambda: None, lambda: False)
    assert pub.connected and box["link"].opened and n == 0
