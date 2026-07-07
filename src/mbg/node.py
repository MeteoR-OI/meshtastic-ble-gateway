# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Mickaël Hoareau — MeteoR-OI
"""Adaptateur node : connexion BLE + routage des messages Client Proxy.

meshtastic-python publie les messages Client Proxy entrants sur le topic pubsub
`meshtastic.mqttclientproxymessage` avec les kwargs (proxymessage, interface).
On s'y abonne et on route vers un callback. Toutes les dépendances externes
(BLEInterface, pub.subscribe/unsubscribe) sont injectables pour les tests.
"""
from __future__ import annotations

import logging
from typing import Callable

log = logging.getLogger("mbg.node")

PROXY_TOPIC = "meshtastic.mqttclientproxymessage"

OnProxy = Callable[[object], None]


def default_interface_factory(address: str):
    """Ouvre une connexion BLE réelle au node (import paresseux de meshtastic)."""
    from meshtastic.ble_interface import BLEInterface

    return BLEInterface(address)


def default_subscribe(handler: Callable, topic: str) -> None:
    from pubsub import pub

    pub.subscribe(handler, topic)


def default_unsubscribe(handler: Callable, topic: str) -> None:
    from pubsub import pub

    pub.unsubscribe(handler, topic)


class MeshtasticNodeLink:
    """Lien BLE vers le node ; délivre chaque ProxyMessage à `on_proxy`."""

    def __init__(
        self,
        address: str,
        on_proxy: OnProxy,
        *,
        interface_factory: Callable[[str], object] = default_interface_factory,
        subscribe: Callable[[Callable, str], None] = default_subscribe,
        unsubscribe: Callable[[Callable, str], None] = default_unsubscribe,
    ) -> None:
        self._address = address
        self._on_proxy = on_proxy
        self._interface_factory = interface_factory
        self._subscribe = subscribe
        self._unsubscribe = unsubscribe
        self._iface = None

    def _handler(self, proxymessage=None, interface=None) -> None:
        """Signature attendue par le pubsub meshtastic (kwargs nommés)."""
        self._on_proxy(proxymessage)

    def open(self) -> None:
        self._subscribe(self._handler, PROXY_TOPIC)
        self._iface = self._interface_factory(self._address)
        log.info("node connecté (BLE %s)", self._address)

    def close(self) -> None:
        self._unsubscribe(self._handler, PROXY_TOPIC)
        if self._iface is not None:
            self._iface.close()
            self._iface = None
