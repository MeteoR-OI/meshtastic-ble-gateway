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

log = logging.getLogger("mbg.runner")

PublisherFactory = Callable[[], object]
NodeLinkFactory = Callable[[str, Callable[[object], None], Callable[[], None]], object]


class ConnectionLost(Exception):
    """Lève une reconnexion quand le lien BLE tombe en cours de session."""


class Gateway:
    """Orchestre publisher + node link ; boucle de reconnexion résiliente."""

    def __init__(
        self,
        config: Config,
        publisher_factory: PublisherFactory,
        nodelink_factory: NodeLinkFactory,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._config = config
        self._publisher_factory = publisher_factory
        self._nodelink_factory = nodelink_factory
        self._sleep = sleep

    def run(self, should_continue: Callable[[], bool]) -> None:
        """Boucle principale : (re)lance une session tant que should_continue()."""
        while should_continue():
            try:
                self._session(should_continue)
            except Exception as exc:  # noqa: BLE001 — toute panne = on retente
                log.warning(
                    "session interrompue: %s — reconnexion dans %ss",
                    exc,
                    self._config.reconnect_delay,
                )
                self._sleep(self._config.reconnect_delay)

    def _session(self, should_continue: Callable[[], bool]) -> None:
        publisher = self._publisher_factory()
        publisher.connect()
        proxy = Proxy(publisher)
        lost = threading.Event()  # armé par meshtastic sur perte du lien BLE
        link = self._nodelink_factory(self._config.ble_address, proxy.on_proxy_message, lost.set)
        link.open()
        try:
            while should_continue():
                if lost.is_set():
                    raise ConnectionLost()
                self._sleep(self._config.poll_interval)
        finally:
            link.close()
            publisher.close()
            log.info("session terminée (%d relayés, %d erreurs)", proxy.forwarded, proxy.errors)
