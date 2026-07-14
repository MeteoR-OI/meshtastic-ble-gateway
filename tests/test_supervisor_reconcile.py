# SPDX-License-Identifier: AGPL-3.0-or-later
"""Réconciliation BLE pré-spawn (opt-in ble_reconcile) : le superviseur nettoie l'état bluez
avant que le worker ne scanne, pour éviter le node stuck-Connected -> scan qui gèle."""
from fakes import FakeWorkerHandle
from mbg.config import Config
from mbg.supervisor import Supervisor


def seq(values):
    it = iter(values)
    return lambda: next(it)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _run_once(config, ble_status, disconnected, slept):
    """Un seul tour de boucle : spawn puis le worker sort seul au 1er tick de surveillance.

    NB : la réconciliation (settle) peut appeler sleep AVANT le 1er spawn -> `workers` vide,
    d'où le garde `if workers`."""
    workers = []

    def spawn(cfg=None):
        w = FakeWorkerHandle(beat_value=1)
        workers.append(w)
        return w

    clock = Clock()

    def sleep_(s):
        slept.append(s)
        clock.t += s
        if workers:
            workers[-1].alive = False  # sort seul -> fin de session, pas de kill

    Supervisor(
        config, spawn, sleep=sleep_, clock=clock, notify=lambda _: None,
        disconnect=disconnected.append, ble_status=ble_status,
    ).run(seq([True, True, False]))
    return workers


def _status(connected=False, paired=True, trusted=False, present=True):
    def fn(mac):
        return {"connected": connected, "paired": paired, "trusted": trusted, "present": present}
    return fn


def test_reconcile_disabled_no_status_call():
    calls = []
    disconnected = []

    def status(mac):
        calls.append(mac)
        return {}

    _run_once(Config(ble_reconcile=False), status, disconnected, [])
    assert calls == []  # ble_reconcile off -> ble_status jamais appelé
    assert disconnected == []


def test_reconcile_connected_forces_disconnect_and_settle():
    disconnected = []
    slept = []
    _run_once(
        Config(ble_reconcile=True, ble_settle=4.0, ble_address="AA:BB"),
        _status(connected=True), disconnected, slept,
    )
    assert disconnected == ["AA:BB"]  # disconnect du node stuck
    assert slept[0] == 4.0             # settle AVANT le reste de la boucle


def test_reconcile_not_connected_no_disconnect():
    disconnected = []
    _run_once(Config(ble_reconcile=True), _status(connected=False), disconnected, [])
    assert disconnected == []  # rien à faire, node déjà libre


def test_reconcile_trusted_warns(caplog):
    disconnected = []
    with caplog.at_level("WARNING"):
        _run_once(Config(ble_reconcile=True, ble_address="AA:BB"),
                  _status(trusted=True), disconnected, [])
    assert any("Trusted=yes" in r.message for r in caplog.records)


def test_reconcile_present_not_paired_warns(caplog):
    disconnected = []
    with caplog.at_level("WARNING"):
        _run_once(Config(ble_reconcile=True, ble_address="AA:BB"),
                  _status(paired=False), disconnected, [])
    assert any("non appairé" in r.message for r in caplog.records)


def test_reconcile_absent_node_noop():
    # bluetoothctl info vide (node inconnu) -> {} -> aucune action
    disconnected = []
    _run_once(Config(ble_reconcile=True), lambda m: {}, disconnected, [])
    assert disconnected == []
