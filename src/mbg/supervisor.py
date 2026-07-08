# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Mickaël Hoareau — MeteoR-OI
"""Superviseur (parent) : pilote un worker BLE jetable, ne touche jamais au BLE.

Il ne peut donc pas geler → il nourrit le watchdog systemd en continu (celui-ci
ne relance que si le PARENT meurt). Il surveille le heartbeat du worker : worker
sorti (os._exit sur drop) → respawn ; worker figé (heartbeat stagnant) → SIGKILL
→ respawn. Backoff plafonné, remis à zéro après un worker qui s'est connecté.
Il expose `submit()` (thread-safe) pour l'API de contrôle, et lance le serveur
HTTP (thread) si un `serve` est fourni.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, Optional

from .config import Config
from .systemd_notify import sd_notify

log = logging.getLogger("mbg.supervisor")

Spawn = Callable[[], object]  # () -> WorkerHandle (beats/is_alive/kill/join/submit)
Notify = Callable[[str], bool]
Serve = Callable[[Callable, Callable], None]  # (submit, should_run) -> bloque jusqu'à should_run False


def _describe(command: Dict[str, Any]) -> str:
    """Résumé concis d'une commande pour le journal d'audit (sans secret)."""
    ctype = command.get("type")
    if ctype == "text":
        text = str(command.get("text", ""))
        snippet = text if len(text) <= 40 else text[:37] + "…"
        return f"texte canal={command.get('channel')} «{snippet}»"
    if ctype == "admin":
        return f"admin {command.get('setting')}={command.get('value')}"
    return str(ctype)


class Supervisor:
    def __init__(
        self,
        config: Config,
        spawn: Spawn,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        notify: Notify = sd_notify,
        serve: Optional[Serve] = None,
    ) -> None:
        self._config = config
        self._spawn = spawn
        self._sleep = sleep
        self._clock = clock
        self._notify = notify
        self._serve = serve
        self._lock = threading.Lock()
        self._current = None  # worker courant, exposé à l'API

    # --- API de contrôle (appelé depuis le thread serveur) ---
    def submit(self, command: Dict[str, Any], timeout: float) -> Dict[str, Any]:
        with self._lock:
            worker = self._current
        label = _describe(command)  # audit INFO ; jamais de token (absent de la commande)
        if worker is None or worker.beats() <= 0:
            log.info("[downlink] %s refusé : aucun worker connecté", label)
            return {"ok": False, "error": "aucun worker connecté"}
        result = worker.submit(command, timeout)
        status = "ok" if result.get("ok") else result.get("error")
        log.info("[downlink] %s → %s (id=%s)", label, status, result.get("id"))
        return result

    def _set_current(self, worker) -> None:
        with self._lock:
            self._current = worker

    # --- Boucle de supervision ---
    def run(self, should_continue: Callable[[], bool]) -> None:
        self._notify("READY=1")
        server_stop = threading.Event()
        if self._serve is not None:
            threading.Thread(
                target=self._serve,
                args=(self.submit, lambda: not server_stop.is_set()),
                name="mbg-api",
                daemon=True,
            ).start()
        try:
            delay = self._config.reconnect_delay
            while should_continue():
                worker = self._spawn()
                self._set_current(worker)
                productive = self._supervise(worker, should_continue)
                self._stop_worker(worker)
                self._set_current(None)
                if not should_continue():
                    break  # arrêt demandé
                if productive:  # le worker s'était connecté -> on repart au délai de base
                    delay = self._config.reconnect_delay
                log.info("respawn du worker dans %ss", delay)
                self._sleep(delay)
                delay = min(delay * 2, self._config.max_reconnect_delay)
        finally:
            server_stop.set()

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
