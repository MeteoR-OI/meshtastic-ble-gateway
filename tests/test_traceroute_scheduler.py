# SPDX-License-Identifier: AGPL-3.0-or-later
import time

from mbg.config import Config
from mbg.traceroute_scheduler import (
    TracerouteScheduler,
    in_quiet_hours,
    parse_quiet_hours,
    select_static,
    select_staleness,
)


# --- Fonctions pures ---------------------------------------------------------
def test_parse_quiet_hours():
    assert parse_quiet_hours("22:00-06:00") == (1320, 360)
    assert parse_quiet_hours("") is None
    assert parse_quiet_hours("   ") is None
    assert parse_quiet_hours("nope") is None            # pas de "-"
    assert parse_quiet_hours("25:00-06:00") is None     # heure hors bornes
    assert parse_quiet_hours("22:60-06:00") is None     # minute hors bornes


def test_in_quiet_hours():
    assert in_quiet_hours(0, None) is False
    # plage overnight 22:00-06:00
    win = (1320, 360)
    assert in_quiet_hours(23 * 60, win) is True     # 23:00 dans la plage
    assert in_quiet_hours(2 * 60, win) is True       # 02:00 dans la plage
    assert in_quiet_hours(12 * 60, win) is False     # midi hors plage
    # plage "normale" 09:00-17:00
    day = (540, 1020)
    assert in_quiet_hours(600, day) is True
    assert in_quiet_hours(60, day) is False


def test_select_static():
    seen = {"a", "b", "c"}
    # tous éligibles -> round-robin
    d, idx = select_static(["a", "b", "c"], 0, eligible=lambda x: x in seen)
    assert (d, idx) == ("a", 1)
    d, idx = select_static(["a", "b", "c"], 1, eligible=lambda x: x in seen)
    assert (d, idx) == ("b", 2)
    # wrap
    d, idx = select_static(["a", "b", "c"], 2, eligible=lambda x: x in seen)
    assert (d, idx) == ("c", 0)
    # liste vide
    assert select_static([], 0, eligible=lambda x: True) == (None, 0)
    # aucun éligible
    assert select_static(["a", "b"], 3, eligible=lambda x: False) == (None, 1)
    # premier non éligible, deuxième oui
    d, idx = select_static(["a", "b"], 0, eligible=lambda x: x == "b")
    assert (d, idx) == ("b", 0)


def test_select_staleness():
    now = 10_000.0
    # 'a' jamais réussi -> priorité max
    assert select_staleness(["a", "b"], {"b": 9_000.0}, [], now) == "a"
    # entre deux tracés : le plus ancien
    assert select_staleness(["a", "b"], {"a": 1_000.0, "b": 9_000.0}, [], now) == "a"
    # pondération priorité : 'b' récent mais prioritaire dépasse 'a'
    assert select_staleness(["a", "b"], {"a": 9_500.0, "b": 9_400.0}, ["b"], now) == "b"
    # aucun candidat
    assert select_staleness([], {}, [], now) is None


# --- Store factice ------------------------------------------------------------
class FakeStore:
    def __init__(self, last_sent=None, per_node=None, success=None, count=0, node=None):
        self._last_sent = last_sent
        self._per_node = per_node or {}
        self._success = success or {}
        self._count = count
        self._node = node or {"channel_util": 10.0}

    def traceroute_last_sent(self):
        return self._last_sent

    def traceroute_last_attempt_by_node(self, dest):
        return self._per_node.get(dest)

    def traceroute_last_success_by_node(self):
        return dict(self._success)

    def traceroute_count_since(self, since, source_prefix=None):
        return self._count

    def latest(self):
        return {"node": self._node}


def _cfg(**kw):
    base = dict(
        traceroute_enabled=True, traceroute_policy="staleness", traceroute_daily_budget=6,
        traceroute_hop_limit=7, traceroute_recent_h=24.0, traceroute_per_node_min_s=21600.0,
        traceroute_min_gap_s=900.0, traceroute_quiet_hours="22:00-06:00",
        traceroute_max_chanutil=40.0, traceroute_tick_s=300.0,
    )
    base.update(kw)
    return Config(**base)


def _sched(config, store, *, now=1000.0, nodes=None, my_num=1, chanutil=10.0, jitter=0.0, localtime=None):
    return TracerouteScheduler(
        config, store,
        nodes_fn=lambda: nodes or {},
        my_num_fn=lambda: my_num,
        chanutil_fn=lambda: chanutil,
        clock=lambda: now,
        localtime=localtime or (lambda t: time.gmtime(t)),
        jitter_fn=lambda: jitter,
    )


def _noon(_ts):
    # struct_time à 12:00 (hors heures calmes) — indépendant du fuseau réel
    return time.struct_time((2026, 7, 14, 12, 0, 0, 1, 195, 0))


def _midnight_3am(_ts):
    return time.struct_time((2026, 7, 14, 3, 0, 0, 1, 195, 0))


def test_due_cadence():
    s = _sched(_cfg(), FakeStore(), now=1000.0)
    assert s.due(1000.0) is True       # 1er tick (last_tick=0)
    assert s.due(1100.0) is False       # trop tôt (<300s)
    assert s.due(1400.0) is True        # 300s plus tard


def test_decide_quiet_hours():
    s = _sched(_cfg(), FakeStore(), localtime=_midnight_3am)
    assert s.decide(1000.0) is None     # 03:00 dans 22:00-06:00


def test_decide_min_gap():
    s = _sched(_cfg(), FakeStore(last_sent=1000.0), now=1500.0, localtime=_noon)
    # min_gap 900 * (1+0) = 900 ; écart 500 < 900 -> skip
    assert s.decide(1500.0) is None


def test_decide_budget_exhausted():
    s = _sched(_cfg(traceroute_daily_budget=2), FakeStore(count=2), localtime=_noon)
    assert s.decide(100_000.0) is None


def test_decide_chanutil_guard():
    s = _sched(_cfg(), FakeStore(), chanutil=55.0, localtime=_noon)
    assert s.decide(100_000.0) is None   # 55% > 40% -> skip
    # chanutil None -> pas de garde (inconnu)
    s2 = _sched(_cfg(), FakeStore(), chanutil=None, localtime=_noon,
                nodes={2: {"lastHeard": 99_999.0, "user": {"id": "!02"}}})
    assert s2.decide(100_000.0) is not None


def test_decide_staleness_full():
    nodes = {
        2: {"lastHeard": 99_000.0, "user": {"id": "!aa"}},
        3: {"lastHeard": 99_000.0, "user": {"id": "!bb"}},
        1: {"lastHeard": 99_000.0, "user": {"id": "!me"}},  # exclu (my_num)
    }
    store = FakeStore(success={"!aa": 90_000.0})  # !bb jamais tracé -> priorité
    s = _sched(_cfg(), store, nodes=nodes, localtime=_noon)
    cmd = s.decide(100_000.0)
    assert cmd["dest"] == "!bb" and cmd["type"] == "traceroute"
    assert cmd["hop_limit"] == 7 and cmd["source"] == "scheduler:staleness"


def test_decide_staleness_no_candidate():
    # node entendu il y a longtemps (> recent_h) -> pas éligible
    nodes = {2: {"lastHeard": 0.0, "user": {"id": "!aa"}}}
    s = _sched(_cfg(), FakeStore(), nodes=nodes, localtime=_noon)
    assert s.decide(100_000.0) is None


def test_decide_staleness_per_node_cooldown():
    nodes = {2: {"lastHeard": 99_999.0, "user": {"id": "!aa"}}}
    # dernière tentative récente -> cooldown per-node -> pas éligible
    store = FakeStore(per_node={"!aa": 99_000.0})
    s = _sched(_cfg(), store, nodes=nodes, localtime=_noon)
    assert s.decide(100_000.0) is None


def test_decide_static_policy():
    store = FakeStore()
    s = _sched(_cfg(traceroute_policy="static", traceroute_targets=("!x1", "!x2")), store, localtime=_noon)
    cmd = s.decide(100_000.0)
    assert cmd["dest"] == "!x1" and cmd["source"] == "scheduler:static"
    cmd2 = s.decide(200_000.0)
    assert cmd2["dest"] == "!x2"


def test_decide_static_node_missing_id_fallback():
    # node sans user.id -> fallback !hex ; couvre la branche d'éligibilité recent
    nodes = {2: {"lastHeard": 99_999.0}}
    s = _sched(_cfg(), FakeStore(), nodes=nodes, localtime=_noon)
    cmd = s.decide(100_000.0)
    assert cmd["dest"] == "!00000002"


def test_decide_unknown_policy():
    s = _sched(_cfg(traceroute_policy="bogus"), FakeStore(), localtime=_noon)
    assert s.decide(100_000.0) is None


def test_decide_node_no_lastheard_skipped():
    nodes = {2: {"user": {"id": "!aa"}}}  # pas de lastHeard -> exclu
    s = _sched(_cfg(), FakeStore(), nodes=nodes, localtime=_noon)
    assert s.decide(100_000.0) is None


def test_poll_ties_due_and_decide():
    s = _sched(_cfg(traceroute_policy="static", traceroute_targets=("!x1",)), FakeStore(),
               now=100_000.0, localtime=_noon)
    assert s.poll()["dest"] == "!x1"   # due (1er tick) + decide
    assert s.poll() is None            # pas dû (même horloge)


def test_jitter_refresh_after_send():
    jit = iter([0.5, 0.0])
    store = FakeStore()
    s = TracerouteScheduler(
        _cfg(traceroute_policy="static", traceroute_targets=("!x1",)), store,
        nodes_fn=lambda: {}, my_num_fn=lambda: 1, chanutil_fn=lambda: 10.0,
        clock=lambda: 100_000.0, localtime=_noon, jitter_fn=lambda: next(jit),
    )
    # jitter initial 0.5 -> effective gap = 900*1.5 = 1350 (pas de last_sent -> passe)
    assert s.decide(100_000.0)["dest"] == "!x1"


def test_default_jitter_fn_used():
    # jitter_fn=None -> RNG par défaut instancié (couvre la branche d'import random)
    s = TracerouteScheduler(
        _cfg(traceroute_policy="static", traceroute_targets=("!x1",)), FakeStore(),
        nodes_fn=lambda: {}, my_num_fn=lambda: 1, chanutil_fn=lambda: 10.0,
        clock=lambda: 100_000.0, localtime=_noon,
    )
    assert 0.0 <= s._jitter < 1.0
