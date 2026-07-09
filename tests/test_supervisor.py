# SPDX-License-Identifier: AGPL-3.0-or-later
import threading

from fakes import FakeWorkerHandle
from mbg.config import Config
from mbg.supervisor import Supervisor
from mbg.tiers import CRITICAL, HIGH, TIER_HIGH_INTERVAL


def seq(values):
    it = iter(values)
    return lambda: next(it)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_no_spawn_when_stopped_immediately():
    spawned = []
    notes = []

    def spawn(cfg=None):
        spawned.append(1)
        return FakeWorkerHandle()

    Supervisor(Config(), spawn, sleep=lambda s: None, clock=lambda: 0.0, notify=notes.append).run(
        lambda: False
    )
    assert spawned == []
    assert notes == ["READY=1"]


def test_productive_exit_then_respawn_at_base_delay():
    workers = []
    slept = []
    notes = []
    clock = Clock()
    step = {"n": 0}

    def spawn(cfg=None):
        w = FakeWorkerHandle()
        workers.append(w)
        return w

    def sleep_(s):
        slept.append(s)
        clock.t += s
        step["n"] += 1
        if step["n"] == 1:
            workers[-1].beat_value = 5  # connecté + a relayé
        elif step["n"] == 2:
            workers[-1].alive = False  # sorti seul (os._exit sur drop)

    Supervisor(
        Config(supervisor_tick=1, reconnect_delay=5, max_reconnect_delay=30),
        spawn, sleep=sleep_, clock=clock, notify=notes.append,
    ).run(seq([True, True, True, True, False]))

    assert len(workers) == 1
    assert workers[0].killed is False  # sorti seul, pas tué
    assert slept == [1, 1, 5]  # 2 ticks de surveillance + respawn au délai de base
    assert "READY=1" in notes and "WATCHDOG=1" in notes


def test_connect_failure_grows_backoff():
    workers = []
    slept = []
    step = {"n": 0}

    def spawn(cfg=None):
        w = FakeWorkerHandle()
        workers.append(w)
        return w

    def sleep_(s):
        slept.append(s)
        step["n"] += 1
        if step["n"] in (1, 3):
            workers[-1].alive = False  # sort tout de suite, beats=0 (connexion échouée)

    Supervisor(
        Config(supervisor_tick=1, reconnect_delay=5, max_reconnect_delay=30),
        spawn, sleep=sleep_, clock=Clock(), notify=lambda _: None,
    ).run(seq([True, True, True, True, True, True, False]))

    assert slept == [1, 5, 1, 10]  # backoff exponentiel : 5 puis 10 (non productif)


def test_frozen_after_connect_is_killed():
    workers = []
    clock = Clock()
    step = {"n": 0}

    def spawn(cfg=None):
        w = FakeWorkerHandle()
        workers.append(w)
        return w

    def sleep_(s):
        clock.t += s
        step["n"] += 1
        if step["n"] == 1:
            workers[-1].beat_value = 5  # connecté... puis plus aucun heartbeat (gel)

    Supervisor(
        Config(supervisor_tick=1, alive_timeout=3, reconnect_delay=5),
        spawn, sleep=sleep_, clock=clock, notify=lambda _: None, disconnect=lambda m: None,
    ).run(seq([True, True, True, True, True, True, True, False]))

    assert workers[0].killed is True
    assert workers[0].joined is True


def test_frozen_during_connect_is_killed():
    workers = []
    clock = Clock()

    def spawn(cfg=None):
        w = FakeWorkerHandle()  # ne bat jamais (beats=0), reste "alive" (bloqué en connexion)
        workers.append(w)
        return w

    def sleep_(s):
        clock.t += s

    Supervisor(
        Config(supervisor_tick=1, connect_grace=2, reconnect_delay=5),
        spawn, sleep=sleep_, clock=clock, notify=lambda _: None, disconnect=lambda m: None,
    ).run(seq([True, True, True, True, True, False]))

    assert workers[0].killed is True  # tué au bout de connect_grace


def test_stop_kills_running_worker():
    workers = []
    clock = Clock()
    step = {"n": 0}

    def spawn(cfg=None):
        w = FakeWorkerHandle()
        workers.append(w)
        return w

    def sleep_(s):
        clock.t += s
        step["n"] += 1
        if step["n"] == 1:
            workers[-1].beat_value = 3  # bat normalement, puis on demande l'arrêt

    Supervisor(
        Config(supervisor_tick=1), spawn, sleep=sleep_, clock=clock, notify=lambda _: None,
        disconnect=lambda m: None,
    ).run(seq([True, True, False, False]))

    assert workers[0].killed is True  # _stop_worker tue le worker encore vivant


def test_kill_forces_ble_disconnect():
    # un worker gelé est SIGKILL -> le superviseur DOIT forcer bluez à lâcher l'ACL (sinon le
    # node n'émet plus et le respawn ne le retrouve pas). Régression du churn terrain (CHAR645).
    workers = []
    clock = Clock()
    disconnected = []

    def spawn(cfg=None):
        w = FakeWorkerHandle()  # ne bat jamais, reste "alive" (bloqué en connexion) -> SIGKILL
        workers.append(w)
        return w

    Supervisor(
        Config(supervisor_tick=1, connect_grace=2, ble_address="CF:87:36:2E:10:5B"),
        spawn, sleep=lambda s: setattr(clock, "t", clock.t + s), clock=clock,
        notify=lambda _: None, disconnect=disconnected.append,
    ).run(seq([True, True, True, True, True, False]))

    assert workers[0].killed is True
    assert disconnected and set(disconnected) == {"CF:87:36:2E:10:5B"}  # disconnect(ble_address) post-kill


def _quiet_sup(**kw):
    return Supervisor(
        Config(), lambda cfg=None: FakeWorkerHandle(), sleep=lambda s: None,
        clock=lambda: 0.0, notify=lambda _: None, **kw,
    )


def test_submit_without_worker():
    r = _quiet_sup().submit({"type": "text"}, 1)
    assert r["ok"] is False and "aucun worker" in r["error"]


def test_submit_worker_not_connected():
    sup = _quiet_sup()
    sup._set_current(FakeWorkerHandle(beat_value=0))  # pas encore connecté
    assert sup.submit({}, 1)["ok"] is False


def test_submit_delegates_to_connected_worker():
    sup = _quiet_sup()
    worker = FakeWorkerHandle(beat_value=5)
    worker.submit_result = {"ok": True, "detail": "ok"}
    sup._set_current(worker)
    r = sup.submit({"type": "text"}, 2)
    assert r == {"ok": True, "detail": "ok"}
    assert worker.submitted == ({"type": "text"}, 2)


def test_submit_long_text_is_truncated_in_audit():
    sup = _quiet_sup()
    sup._set_current(FakeWorkerHandle(beat_value=5))
    # texte > 40 caractères -> branche de troncature du résumé d'audit
    r = sup.submit({"type": "text", "text": "x" * 80, "channel": "Fr_Balise"}, 1)
    assert r["ok"] is True


def test_submit_admin_command_and_error_result():
    sup = _quiet_sup()
    worker = FakeWorkerHandle(beat_value=5)
    worker.submit_result = {"ok": False, "error": "boom"}  # branche résultat-erreur
    sup._set_current(worker)
    r = sup.submit({"type": "admin", "setting": "role", "value": "ROUTER"}, 1)  # branche _describe admin
    assert r["ok"] is False


def test_submit_directed_command_audit():
    sup = _quiet_sup()
    worker = FakeWorkerHandle(beat_value=5)
    worker.submit_result = {"ok": True}
    sup._set_current(worker)
    # commande dirigée (dest) -> branche _describe « ctype → dest »
    r = sup.submit({"type": "request_position", "dest": "!42cd37a3"}, 1)
    assert r["ok"] is True


class FakeStore:
    def __init__(self):
        self.links = []
        self.pruned = []
        self.exported = []

    def record_link(self, reconnects):
        self.links.append(reconnects)

    def prune(self, seconds):
        self.pruned.append(seconds)

    def export_csv(self, directory):
        self.exported.append(directory)


class FakeLatestStore(FakeStore):
    """Store dont on pilote la dernière batterie (source des paliers V0.4)."""

    def __init__(self, battery=None):
        super().__init__()
        self.battery = battery

    def latest(self):
        node = {"battery_level": self.battery} if self.battery is not None else None
        return {"node": node, "link": None}


def _bare_sup(config, **kw):
    return Supervisor(
        config, lambda cfg=None: None, sleep=lambda s: None, clock=lambda: 0.0,
        notify=lambda _: None, **kw,
    )


# --- Paliers batterie (V0.4) ---

def test_plan_tier_static_when_disabled():
    tier = _bare_sup(Config(battery_tiers=False, monitor_interval=42))._plan_tier()
    assert tier.duty_cycle is False and tier.monitor_interval == 42


def test_plan_tier_reads_battery():
    store = FakeLatestStore(battery=10)
    sup = _bare_sup(Config(battery_tiers=True, monitor_interval=300, tier_hysteresis=3), store=store)
    assert sup._plan_tier().name == "CRITICAL"
    store.battery = 90
    assert sup._plan_tier().name == "HIGH"  # remonte (hystérésis franchie)


def test_plan_tier_no_store_defaults_high():
    # battery_tiers on mais pas de store -> batterie inconnue -> défaut HIGH
    assert _bare_sup(Config(battery_tiers=True))._plan_tier().name == "HIGH"


def test_effective_config_critical_uses_duty_on_and_forces_telemetry():
    sup = _bare_sup(Config(duty_on=7, force_telemetry=False))
    eff = sup._effective_config(CRITICAL, announce=True)  # None -> duty_on ; changement -> télémétrie
    assert eff.monitor_interval == 7 and eff.force_telemetry is True


def test_effective_config_high_keeps_interval_and_no_forced_telemetry():
    eff = _bare_sup(Config(force_telemetry=False))._effective_config(HIGH, announce=False)
    assert eff.monitor_interval == TIER_HIGH_INTERVAL and eff.force_telemetry is False


def test_wait_feeds_watchdog_each_tick():
    clock = Clock()
    notes = []
    sup = Supervisor(
        Config(supervisor_tick=1), lambda cfg=None: None,
        sleep=lambda s: setattr(clock, "t", clock.t + s), clock=clock, notify=notes.append,
    )
    sup._wait(3, lambda: True)
    assert notes.count("WATCHDOG=1") == 3  # 3 ticks pour couvrir 3 s (OFF watchdog-friendly)


def test_wait_stops_on_should_continue_false():
    clock = Clock()
    notes = []
    sup = Supervisor(
        Config(supervisor_tick=1), lambda cfg=None: None,
        sleep=lambda s: setattr(clock, "t", clock.t + s), clock=clock, notify=notes.append,
    )
    sup._wait(100, seq([True, False]))  # arrêt demandé avant l'échéance
    assert notes.count("WATCHDOG=1") == 1


def test_on_window_cuts_connected_session():
    clock = Clock()
    sup = Supervisor(
        Config(supervisor_tick=1, alive_timeout=15), lambda cfg=None: None,
        sleep=lambda s: setattr(clock, "t", clock.t + s), clock=clock, notify=lambda _: None,
    )
    worker = FakeWorkerHandle(beat_value=5)  # connecté
    productive = sup._supervise(worker, seq([True] * 10), on_window=3)
    assert productive is True  # coupure volontaire = session productive
    assert worker.killed is False  # pas un SIGKILL (arrêt propre par le superviseur)


def test_duty_cycle_run_cuts_off_and_records():
    spawned = []
    clock = Clock()
    notes = []
    store = FakeLatestStore(battery=10)  # CRITICAL

    def spawn(cfg):
        spawned.append(cfg)
        w = FakeWorkerHandle()  # beats=0
        w.alive = False  # drop immédiat -> non productif, on passe direct au OFF
        return w

    calls = {"n": 0}

    def cont():  # borne l'exécution à un cycle complet (ON éclair + OFF)
        calls["n"] += 1
        return calls["n"] <= 8

    Supervisor(
        Config(battery_tiers=True, monitor_interval=300, duty_on=3, duty_off=5,
               supervisor_tick=1, tier_hysteresis=3),
        spawn, sleep=lambda s: setattr(clock, "t", clock.t + s), clock=clock,
        notify=notes.append, store=store,
    ).run(cont)

    assert spawned[0].monitor_interval == 3        # CRITICAL -> cadence = duty_on
    assert spawned[0].force_telemetry is True       # changement de mode -> télémétrie forcée
    assert store.links == [1]                        # une reconnexion enregistrée
    assert notes.count("WATCHDOG=1") >= 3            # le OFF nourrit le watchdog


def test_mode_change_forces_telemetry_once():
    # palier HIGH (lien live) : la télémétrie n'est forcée qu'à la 1re session (changement de
    # mode) ; une fois le palier annoncé sur une session productive, on ne force plus.
    spawned = []
    workers = []
    store = FakeLatestStore(battery=90)  # HIGH

    def spawn(cfg):
        spawned.append(cfg)
        w = FakeWorkerHandle(beat_value=5)  # connecté (productif)
        workers.append(w)
        return w

    def sleep_(s):
        workers[-1].alive = False  # drop après le 1er tick -> session productive terminée

    calls = {"n": 0}

    def cont():
        calls["n"] += 1
        return calls["n"] <= 6  # exactement 2 sessions

    Supervisor(
        Config(battery_tiers=True, monitor_interval=300, tier_hysteresis=3, reconnect_delay=5),
        spawn, sleep=sleep_, clock=Clock(), notify=lambda _: None, store=store,
    ).run(cont)

    assert spawned[0].force_telemetry is True   # 1re session = changement de mode -> télémétrie
    assert spawned[1].force_telemetry is False  # palier déjà annoncé -> plus de forçage
    assert store.links == [1, 2]


def test_record_link_on_respawn():
    store = FakeStore()
    workers = []
    step = {"n": 0}

    def spawn(cfg=None):
        w = FakeWorkerHandle()
        workers.append(w)
        return w

    def sleep_(s):
        step["n"] += 1
        if step["n"] == 1:
            workers[-1].alive = False  # sort non-productif -> respawn

    Supervisor(
        Config(supervisor_tick=1, reconnect_delay=5), spawn, sleep=sleep_,
        clock=Clock(), notify=lambda _: None, store=store,
    ).run(seq([True, True, True, False]))
    assert store.links == [1]  # une reconnexion enregistrée


def test_maintenance_prunes_and_exports():
    store = FakeStore()
    sup = Supervisor(
        Config(dump_dir="/x", retention_days=2), lambda: None,
        sleep=lambda s: None, clock=lambda: 0.0, notify=lambda _: None, store=store,
    )
    ticks = iter([True, False])
    sup._maintenance(lambda: next(ticks))
    assert store.pruned == [2 * 86400] and store.exported == ["/x"]


def test_maintenance_without_dump_or_retention():
    store = FakeStore()
    sup = Supervisor(
        Config(dump_dir=None, retention_days=0), lambda: None,
        sleep=lambda s: None, clock=lambda: 0.0, notify=lambda _: None, store=store,
    )
    ticks = iter([True, False])
    sup._maintenance(lambda: next(ticks))
    assert store.pruned == [] and store.exported == []


def test_run_starts_maintenance_thread():
    store = FakeStore()
    sup = Supervisor(
        Config(dump_dir="/x", dump_interval=1), lambda cfg=None: FakeWorkerHandle(),
        sleep=lambda s: None, clock=lambda: 0.0, notify=lambda _: None, store=store,
    )
    sup.run(lambda: False)  # démarre le thread de maintenance puis sort aussitôt


def test_run_starts_api_server_thread():
    done = threading.Event()
    captured = {}

    def serve(submit, should_run):
        captured["submit"] = submit
        captured["run"] = should_run()
        done.set()

    sup = Supervisor(
        Config(), lambda cfg=None: FakeWorkerHandle(), sleep=lambda s: None,
        clock=lambda: 0.0, notify=lambda _: None, serve=serve,
    )
    sup.run(lambda: False)  # pas de spawn ; le thread serveur démarre quand même
    assert done.wait(2)
    assert captured["submit"] == sup.submit
