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
from dataclasses import replace
from typing import Any, Callable, Dict, Optional

from .config import Config
from .systemd_notify import sd_notify
from .tiers import Tier, select_tier

log = logging.getLogger("mbg.supervisor")

Spawn = Callable[[Config], object]  # (config) -> WorkerHandle (beats/is_alive/kill/join/submit)
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
    dest = command.get("dest")
    if dest:  # commande dirigée (télémétrie/position vers un node distant)
        return f"{ctype} → {dest}"
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
        store=None,
    ) -> None:
        self._config = config
        self._spawn = spawn
        self._sleep = sleep
        self._clock = clock
        self._notify = notify
        self._serve = serve
        self._store = store
        self._lock = threading.Lock()
        self._current = None  # worker courant, exposé à l'API
        self._tier: Optional[Tier] = None  # palier courant (état pour l'hystérésis)
        self._announced_tier: Optional[Tier] = None  # dernier palier annoncé (télémétrie diffusée)

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

    # --- Paliers batterie (V0.4) ---
    def _plan_tier(self) -> Tier:
        """Palier courant. Statique (comportement V0.3) si `battery_tiers` off."""
        if not self._config.battery_tiers:
            return Tier("STATIC", self._config.monitor_interval, False)
        node = (self._store.latest().get("node") if self._store is not None else None) or {}
        level = node.get("battery_level")
        new = select_tier(level, self._tier, self._config.tier_hysteresis)
        if new != self._tier:  # transition -> tracé pour l'observabilité terrain
            log.info("palier batterie → %s (batterie=%s%%)", new.name, level)
        self._tier = new
        return new

    def _effective_config(self, tier: Tier, announce: bool) -> Config:
        """Config du prochain worker : cadence du palier ; télémétrie forcée si changement de mode."""
        # CRITICAL (monitor_interval None) -> un seul relevé par fenêtre de connexion (duty_on).
        interval = tier.monitor_interval if tier.monitor_interval is not None else self._config.duty_on
        return replace(
            self._config, monitor_interval=interval, force_telemetry=self._config.force_telemetry or announce
        )

    def _wait(self, duration: float, should_continue: Callable[[], bool]) -> None:
        """Attente qui continue de nourrir le watchdog (le OFF du duty-cycle dépasse WatchdogSec)."""
        end = self._clock() + duration
        while should_continue() and self._clock() < end:
            self._notify("WATCHDOG=1")
            self._sleep(self._config.supervisor_tick)

    def _maintenance(self, should_run: Callable[[], bool]) -> None:
        """Thread : purge + export CSV périodiques de la base de métriques."""
        while should_run():
            self._sleep(self._config.dump_interval)
            if self._config.retention_days > 0:
                self._store.prune(self._config.retention_days * 86400)
            if self._config.dump_dir:
                self._store.export_csv(self._config.dump_dir)

    # --- Boucle de supervision ---
    def run(self, should_continue: Callable[[], bool]) -> None:
        self._notify("READY=1")
        stop = threading.Event()
        should_run = lambda: not stop.is_set()  # noqa: E731
        if self._serve is not None:
            threading.Thread(
                target=self._serve, args=(self.submit, should_run), name="mbg-api", daemon=True
            ).start()
        if self._store is not None and (self._config.dump_dir or self._config.retention_days > 0):
            threading.Thread(target=self._maintenance, args=(should_run,), name="mbg-maint", daemon=True).start()
        try:
            delay = self._config.reconnect_delay
            reconnects = 0
            while should_continue():
                tier = self._plan_tier()
                announce = self._config.battery_tiers and tier != self._announced_tier
                worker = self._spawn(self._effective_config(tier, announce))
                self._set_current(worker)
                on_window = self._config.duty_on if tier.duty_cycle else None
                productive = self._supervise(worker, should_continue, on_window)
                self._stop_worker(worker)
                self._set_current(None)
                if productive and announce:  # télémétrie diffusée pendant la session -> palier annoncé
                    self._announced_tier = tier
                if not should_continue():
                    break  # arrêt demandé
                reconnects += 1
                if self._store is not None:
                    self._store.record_link(reconnects)  # timeline des reconnexions (qualité BLE)
                if tier.duty_cycle:  # palier critique : on coupe le lien pour laisser le node dormir
                    # OFF long (>> WatchdogSec) -> attente qui continue de nourrir le watchdog.
                    log.info("palier %s : lien coupé %ss (duty-cycle)", tier.name, self._config.duty_off)
                    self._wait(self._config.duty_off, should_continue)
                else:
                    if productive:  # le worker s'était connecté -> on repart au délai de base
                        delay = self._config.reconnect_delay
                    # Backoff court (<= max_reconnect_delay, défaut 30s < WatchdogSec) : sleep simple.
                    log.info("respawn du worker dans %ss", delay)
                    self._sleep(delay)
                    delay = min(delay * 2, self._config.max_reconnect_delay)
        finally:
            stop.set()

    def _supervise(self, worker, should_continue: Callable[[], bool], on_window: Optional[float] = None) -> bool:
        """Surveille jusqu'à fin/gel. Renvoie True si le worker s'était connecté (beats>0).

        `on_window` (duty-cycle) : une fois connecté, on coupe volontairement la session au
        bout de `on_window` s (le node peut alors dormir pendant le OFF).
        """
        last_beats = worker.beats()
        last_progress = self._clock()
        start = self._clock()
        while should_continue():
            self._notify("WATCHDOG=1")  # le parent est vivant tant qu'il surveille
            self._sleep(self._config.supervisor_tick)
            if on_window is not None and worker.beats() > 0 and self._clock() - start > on_window:
                log.info("fenêtre ON écoulée (%ss) — fin de session duty-cycle", on_window)
                return True  # connecté = productif ; coupure volontaire du duty-cycle
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
