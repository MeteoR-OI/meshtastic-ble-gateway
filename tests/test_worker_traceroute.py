# SPDX-License-Identifier: AGPL-3.0-or-later
from fakes import FakeCounter
from mbg.config import Config
from mbg.worker import _worker_body


class FakePub:
    def __init__(self, *a):
        pass

    def publish(self, topic, payload):
        pass


class FakeLink:
    def __init__(self, addr, on_proxy, on_lost):
        pass

    def send_traceroute(self, dest, hop, ch):
        return 1

    def publish(self, *a):
        pass

    def node_id_of(self, num):
        return "!x"

    def gateway_id(self):
        return "!gw"

    def nodes(self):
        return {}

    def my_num(self):
        return 1


class FakeStore:
    def __init__(self, path):
        self.path = path
        self._node = {"channel_util": 12.0}

    def latest(self):
        return {"node": self._node}


class FakeCoordinator:
    last = None

    def __init__(self, **kw):
        FakeCoordinator.last = kw


class FakeScheduler:
    last = None

    def __init__(self, config, store, **kw):
        FakeScheduler.last = (config, store, kw)


def _run(config, session):
    return _worker_body(
        config, FakeCounter(), session=session,
        publisher_cls=FakePub, nodelink_cls=FakeLink, store_cls=FakeStore,
        coordinator_cls=FakeCoordinator, scheduler_cls=FakeScheduler,
        clock=lambda: 42.0,
    )


def test_traceroute_setup_endpoint_only():
    # api_token seul -> traceroute_active True, coordinateur monté, PAS de scheduler
    captured = {}

    def session(config, pf, nf, hb, sc, commands=None, monitor=None, tune=None, traceroute_setup=None):
        pub = FakePub()
        link = FakeLink(None, None, None)
        captured["result"] = traceroute_setup(link, pub)

    _run(Config(api_token="tok", monitor_interval=0, traceroute_topic="mbg/traceroute"), session)
    coord, sched = captured["result"]
    assert sched is None
    kw = FakeCoordinator.last
    assert kw["topic"] == "mbg/traceroute" and kw["clock"]() == 42.0
    assert kw["send_fn"](0, 0, 0) == 1 and kw["id_of"](2) == "!x" and kw["gateway_id_fn"]() == "!gw"


def test_traceroute_setup_with_scheduler():
    captured = {}

    def session(config, pf, nf, hb, sc, commands=None, monitor=None, tune=None, traceroute_setup=None):
        captured["result"] = traceroute_setup(FakeLink(None, None, None), FakePub())

    _run(Config(traceroute_enabled=True, api_token=None, monitor_interval=0), session)
    coord, sched = captured["result"]
    assert sched is not None
    cfg, store, kw = FakeScheduler.last
    assert cfg.traceroute_enabled is True
    # chanutil_fn lit store.latest().node.channel_util
    assert kw["chanutil_fn"]() == 12.0
    assert kw["nodes_fn"]() == {} and kw["my_num_fn"]() == 1


def test_no_traceroute_setup_when_inactive():
    seen = {}

    def session(config, pf, nf, hb, sc, commands=None, monitor=None, tune=None, traceroute_setup=None):
        seen["setup"] = traceroute_setup

    _run(Config(api_token=None, traceroute_enabled=False, monitor_interval=0), session)
    assert seen["setup"] is None


def test_chanutil_fn_none_when_no_node():
    captured = {}

    def session(config, pf, nf, hb, sc, commands=None, monitor=None, tune=None, traceroute_setup=None):
        traceroute_setup(FakeLink(None, None, None), FakePub())
        captured["ok"] = True

    # store.latest() sans node -> chanutil None
    class EmptyStore(FakeStore):
        def latest(self):
            return {"node": None}

    _worker_body(
        Config(traceroute_enabled=True, monitor_interval=0), FakeCounter(), session=session,
        publisher_cls=FakePub, nodelink_cls=FakeLink, store_cls=EmptyStore,
        coordinator_cls=FakeCoordinator, scheduler_cls=FakeScheduler, clock=lambda: 1.0,
    )
    assert FakeScheduler.last[2]["chanutil_fn"]() is None
