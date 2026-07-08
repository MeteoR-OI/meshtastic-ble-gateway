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
from typing import Callable, Optional

from .control import execute_command

log = logging.getLogger("mbg.node")

PROXY_TOPIC = "meshtastic.mqttclientproxymessage"
CONNECTION_LOST_TOPIC = "meshtastic.connection.lost"

OnProxy = Callable[[object], None]
OnLost = Callable[[], None]


def default_interface_factory(address: str):
    """Ouvre une connexion BLE réelle au node (import paresseux de meshtastic)."""
    from meshtastic.ble_interface import BLEInterface

    return BLEInterface(address)


def default_liveness(iface: object) -> bool:
    """Vrai si le lien BLE est encore up, d'après l'état D-Bus BlueZ (via bleak).

    C'est LE signal qui détecte la coupure silencieuse : meshtastic ne lève ni
    exception ni `connection.lost`, mais BlueZ, lui, sait que `Connected: no`.
    Fail-open (True) si l'introspection échoue, pour ne jamais reconnecter à tort.
    """
    client = getattr(iface, "client", None)
    bleak_client = getattr(client, "bleak_client", None)
    connected = getattr(bleak_client, "is_connected", None)
    if connected is None:
        return True
    return bool(connected)


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
        on_lost: Optional[OnLost] = None,
        *,
        interface_factory: Callable[[str], object] = default_interface_factory,
        subscribe: Callable[[Callable, str], None] = default_subscribe,
        unsubscribe: Callable[[Callable, str], None] = default_unsubscribe,
        liveness: Callable[[object], bool] = default_liveness,
        executor: Callable[[object, dict], dict] = execute_command,
    ) -> None:
        self._address = address
        self._on_proxy = on_proxy
        self._on_lost = on_lost
        self._interface_factory = interface_factory
        self._subscribe = subscribe
        self._unsubscribe = unsubscribe
        self._liveness = liveness
        self._executor = executor
        self._iface = None

    def _handler(self, proxymessage=None, interface=None) -> None:
        """Signature attendue par le pubsub meshtastic (kwargs nommés)."""
        self._on_proxy(proxymessage)

    def _handler_lost(self, interface=None) -> None:
        """Perte du lien BLE signalée par meshtastic-python."""
        log.warning("lien BLE perdu (node %s)", self._address)
        if self._on_lost is not None:
            self._on_lost()

    def is_alive(self) -> bool:
        """Sonde de vivacité du lien BLE (sans I/O). False si non ouvert ou lien mort."""
        if self._iface is None:
            return False
        return self._liveness(self._iface)

    def send(self, command: dict) -> dict:
        """Exécute une commande downlink sur l'interface (write BLE). Voir `control`."""
        return self._executor(self._iface, command)

    def open(self) -> None:
        self._subscribe(self._handler, PROXY_TOPIC)
        self._subscribe(self._handler_lost, CONNECTION_LOST_TOPIC)
        self._iface = self._interface_factory(self._address)
        log.info("node connecté (BLE %s)", self._address)

    def close(self) -> None:
        self._unsubscribe(self._handler, PROXY_TOPIC)
        self._unsubscribe(self._handler_lost, CONNECTION_LOST_TOPIC)
        if self._iface is not None:
            self._iface.close()
            self._iface = None
