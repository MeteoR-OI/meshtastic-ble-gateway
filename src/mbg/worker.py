# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Mickaël Hoareau — MeteoR-OI
"""Worker : le sous-processus jetable qui fait le BLE.

Il tourne UNE session (`run_one_session`) et sort en `os._exit` — DUR, pour ne
jamais appeler le `close()` meshtastic qui gèle sur lien mort. Sortie du worker →
le superviseur respawn. Émet un heartbeat (compteur partagé) à chaque poll.
"""
from __future__ import annotations

import logging
import os
import signal
from typing import Callable

from .config import Config
from .mqtt_publisher import PahoPublisher
from .node import MeshtasticNodeLink
from .session import run_one_session

log = logging.getLogger("mbg.worker")

EXIT_RESPAWN = 0  # code de sortie : rien d'anormal, le parent respawn


def _worker_body(
    config: Config,
    beat_counter,
    *,
    session: Callable = run_one_session,
    publisher_cls=PahoPublisher,
    nodelink_cls=MeshtasticNodeLink,
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

    try:
        session(config, publisher_factory, nodelink_factory, heartbeat, lambda: True)
    except Exception as exc:  # noqa: BLE001 — toute panne = fin de session, le parent respawn
        log.warning("session worker interrompue : %s", exc)
    return EXIT_RESPAWN


def run_worker(config: Config, beat_counter) -> None:  # pragma: no cover — frontière process/OS
    """Point d'entrée du sous-processus. Sort en os._exit (jamais de close() qui gèle)."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s [worker] %(message)s"
    )
    signal.signal(signal.SIGTERM, lambda *_: os._exit(EXIT_RESPAWN))
    os._exit(_worker_body(config, beat_counter))
