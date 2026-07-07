# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Mickaël Hoareau — MeteoR-OI
"""Superviseur (parent) : pilote un worker BLE jetable, ne touche jamais au BLE.

Il ne peut donc pas geler → il nourrit le watchdog systemd en continu (celui-ci
ne relance que si le PARENT meurt). Il surveille le heartbeat du worker : worker
sorti (os._exit sur drop) → respawn ; worker figé (heartbeat stagnant) → SIGKILL
→ respawn. Backoff plafonné, remis à zéro après un worker qui s'est connecté.
"""
from __future__ import annotations

import logging
import time
from typing import Callable

from .config import Config
from .systemd_notify import sd_notify

log = logging.getLogger("mbg.supervisor")

Spawn = Callable[[], object]  # () -> WorkerHandle (beats/is_alive/kill/join)
Notify = Callable[[str], bool]


class Supervisor:
    def __init__(
        self,
        config: Config,
        spawn: Spawn,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        notify: Notify = sd_notify,
    ) -> None:
        self._config = config
        self._spawn = spawn
        self._sleep = sleep
        self._clock = clock
        self._notify = notify

    def run(self, should_continue: Callable[[], bool]) -> None:
        self._notify("READY=1")
        delay = self._config.reconnect_delay
        while should_continue():
            worker = self._spawn()
            productive = self._supervise(worker, should_continue)
            self._stop_worker(worker)
            if not should_continue():
                break  # arrêt demandé
            if productive:  # le worker s'était connecté -> on repart au délai de base
                delay = self._config.reconnect_delay
            log.info("respawn du worker dans %ss", delay)
            self._sleep(delay)
            delay = min(delay * 2, self._config.max_reconnect_delay)

    def _supervise(self, worker, should_continue: Callable[[], bool]) -> bool:
        """Surveille jusqu'à fin/gel. Renvoie True si le worker s'était connecté (beats>0)."""
        last_beats = worker.beats()
        last_progress = self._clock()
        while should_continue():
            self._notify("WATCHDOG=1")  # le parent est vivant tant qu'il surveille
            self._sleep(self._config.supervisor_tick)
            if not worker.is_alive():
                return worker.beats() > 0  # sorti seul (os._exit sur drop)
            beats = worker.beats()
            if beats > last_beats:
                last_beats = beats
                last_progress = self._clock()
            else:
                # Pas de heartbeat : grâce longue tant que non connecté, courte ensuite.
                grace = self._config.alive_timeout if beats > 0 else self._config.connect_grace
                if self._clock() - last_progress > grace:
                    log.warning("worker figé (%s) — SIGKILL", "connecté" if beats > 0 else "connexion")
                    worker.kill()
                    worker.join()
                    return beats > 0
        return worker.beats() > 0  # arrêt demandé

    def _stop_worker(self, worker) -> None:
        if worker.is_alive():
            worker.kill()
            worker.join()
