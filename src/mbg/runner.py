# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Mickaël Hoareau — MeteoR-OI
"""Superviseur : maintient une session BLE↔broker, reconnecte en cas de coupure."""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from .config import Config
from .proxy import Proxy
from .systemd_notify import sd_notify

log = logging.getLogger("mbg.runner")

PublisherFactory = Callable[[], object]
NodeLinkFactory = Callable[[str, Callable[[object], None], Callable[[], None]], object]
Notify = Callable[[str], bool]


class Gateway:
    """Orchestre publisher + node link ; sessions résilientes + watchdog systemd."""

    def __init__(
        self,
        config: Config,
        publisher_factory: PublisherFactory,
        nodelink_factory: NodeLinkFactory,
        *,
        sleep: Callable[[float], None] = time.sleep,
        notify: Notify = sd_notify,
    ) -> None:
        self._config = config
        self._publisher_factory = publisher_factory
        self._nodelink_factory = nodelink_factory
        self._sleep = sleep
        self._notify = notify

    def run(self, should_continue: Callable[[], bool]) -> None:
        """(Re)lance une session tant que should_continue() ; backoff plafonné, bruyant."""
        self._notify("READY=1")
        delay = self._config.reconnect_delay
        attempt = 0
        while should_continue():
            self._notify("WATCHDOG=1")  # vivant même entre deux tentatives
            try:
                forwarded = self._session(should_continue)
            except Exception as exc:  # noqa: BLE001 — échec d'ouverture = on retente
                forwarded = 0
                log.warning("échec de session : %s", exc)
            else:
                if not should_continue():
                    break  # arrêt demandé — pas de reconnexion
                log.warning("lien BLE perdu (%d relayés dans la session)", forwarded)
            if forwarded:  # session qui a produit -> on repart au délai de base
                delay = self._config.reconnect_delay
                attempt = 0
            attempt += 1
            log.info("reconnexion #%d dans %ss", attempt, delay)
            self._sleep(delay)
            delay = min(delay * 2, self._config.max_reconnect_delay)

    def _session(self, should_continue: Callable[[], bool]) -> int:
        publisher = self._publisher_factory()
        publisher.connect()
        proxy = Proxy(publisher)
        lost = threading.Event()  # armé par meshtastic SI connection.lost est émis
        link = self._nodelink_factory(self._config.ble_address, proxy.on_proxy_message, lost.set)
        link.open()
        try:
            while should_continue():
                # Coupure signalée (lost) OU silencieuse (sonde de vivacité) -> on sort.
                if lost.is_set() or not link.is_alive():
                    break
                self._notify("WATCHDOG=1")
                self._sleep(self._config.poll_interval)
        finally:
            link.close()
            publisher.close()
            log.info("session terminée (%d relayés, %d erreurs)", proxy.forwarded, proxy.errors)
        return proxy.forwarded
