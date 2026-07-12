# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Mickaël Hoareau — MeteoR-OI
"""Worker : le sous-processus jetable qui fait le BLE.

Il tourne UNE session (`run_one_session`) et sort en `os._exit` — DUR, pour ne
jamais appeler le `close()` meshtastic qui gèle sur lien mort. Sortie du worker →
le superviseur respawn. Émet un heartbeat (compteur partagé) à chaque poll et
exécute les commandes downlink reçues via les queues.
"""
from __future__ import annotations

import logging
import os
import queue
import signal
import time
from typing import Callable

from . import metrics as metrics_mod
from .config import Config
from .link_tuner import tune_link
from .mqtt_publisher import PahoPublisher
from .node import MeshtasticNodeLink
from .session import run_one_session
from .storage import MetricsStore

log = logging.getLogger("mbg.worker")

EXIT_RESPAWN = 0  # code de sortie : rien d'anormal, le parent respawn


class QueueCommandChannel:
    """Vue worker des queues de commandes (drain) / résultats (reply)."""

    def __init__(self, cmd_q, res_q) -> None:
        self._cmd_q = cmd_q
        self._res_q = res_q

    def drain(self):
        out = []
        while True:
            try:
                out.append(self._cmd_q.get_nowait())
            except queue.Empty:
                return out

    def reply(self, cmd_id, result) -> None:
        self._res_q.put({"id": cmd_id, **result})


def _worker_body(
    config: Config,
    beat_counter,
    commands=None,
    *,
    session: Callable = run_one_session,
    publisher_cls=PahoPublisher,
    nodelink_cls=MeshtasticNodeLink,
    store_cls=MetricsStore,
    tuner: Callable[[Config], bool] = tune_link,
    clock: Callable[[], float] = time.time,
) -> int:
    """Logique testable du worker (sans os._exit / signaux)."""

    def heartbeat() -> None:
        with beat_counter.get_lock():
            beat_counter.value += 1

    def publisher_factory():
        return publisher_cls(
            config.broker_host, config.broker_port, config.broker_username, config.broker_password
        )

    def nodelink_factory(address, on_proxy, on_lost):
        return nodelink_cls(address, on_proxy, on_lost)

    monitor = None
    if config.monitor_interval > 0:
        store = store_cls(config.db_path)
        # Fenêtre "voisin actif" (V0.8.2) : constante par session (dépend de la config).
        active_window = metrics_mod.resolve_active_window(
            config.monitor_interval, config.neighbor_active_secs
        )

        def monitor(link):
            if config.force_telemetry:
                link.send({"type": "telemetry"})  # mesure fraîche (write BLE, peut geler->SIGKILL)
            # `now` = horloge murale (comparée au lastHeard unix des voisins) pour ne relever
            # que les voisins ACTIFS ; le filtre s'applique à l'extraction (cf. read_metrics).
            data = link.read_metrics(now=clock(), active_window=active_window)
            store.record_node(data["node"], data["position"])
            store.record_neighbors(data["neighbors"])

    # Stabilisation du lien BLE (V0.5) : opt-in via ble_supervision_timeout_ms > 0.
    tune = None
    if config.ble_supervision_timeout_ms > 0:
        def tune():
            tuner(config)

    try:
        session(
            config, publisher_factory, nodelink_factory, heartbeat, lambda: True,
            commands=commands, monitor=monitor, tune=tune,
        )
    except Exception as exc:  # noqa: BLE001 — toute panne = fin de session, le parent respawn
        log.warning("session worker interrompue : %s", exc)
    return EXIT_RESPAWN


def run_worker(config, counter, cmd_q, res_q) -> None:  # pragma: no cover — frontière process/OS
    """Point d'entrée du sous-processus. Sort en os._exit (jamais de close() qui gèle)."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s [worker] %(message)s"
    )
    signal.signal(signal.SIGTERM, lambda *_: os._exit(EXIT_RESPAWN))
    commands = QueueCommandChannel(cmd_q, res_q)
    os._exit(_worker_body(config, counter, commands))
