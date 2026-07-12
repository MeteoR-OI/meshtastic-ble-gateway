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
import threading
from typing import Any, Callable, Optional

from . import metrics
from .control import execute_command

log = logging.getLogger("mbg.node")

PROXY_TOPIC = "meshtastic.mqttclientproxymessage"
CONNECTION_LOST_TOPIC = "meshtastic.connection.lost"
RECEIVE_TOPIC = "meshtastic.receive"  # tous les paquets décodés reçus
ROUTING_PORTNUM = "ROUTING_APP"  # portnum d'un accusé (ACK/NAK)
ACK_TIMEOUT = 60.0  # au-delà, on logue un « timeout » d'accusé radio (want_ack)

OnProxy = Callable[[object], None]
OnLost = Callable[[], None]


def _ack_status(packet: Any) -> str:
    """Interprète un paquet ROUTING en accusé radio lisible."""
    try:
        reason = packet["decoded"]["routing"]["errorReason"]
    except (KeyError, TypeError):
        return "reçu (ACK)"  # pas d'erreur de routage -> livré
    if reason in (0, "NONE", None):
        return "reçu (ACK)"
    return f"échec ({reason})"


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
        timer_factory: Callable[[float, Callable[[], None]], Any] = threading.Timer,
    ) -> None:
        self._address = address
        self._on_proxy = on_proxy
        self._on_lost = on_lost
        self._interface_factory = interface_factory
        self._subscribe = subscribe
        self._unsubscribe = unsubscribe
        self._liveness = liveness
        self._executor = executor
        self._timer_factory = timer_factory
        self._iface = None
        self._pending_acks = {}  # packet_id -> (label, timer) pour want_ack
        self._ack_lock = threading.Lock()

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

    def read_metrics(self, *, now: Optional[float] = None, active_window: Optional[float] = None) -> dict:
        """Relève les métriques du node (device, identité, statut MQTT, position, voisins) — sans I/O radio.

        `now`/`active_window` (V0.8.2) : ne comptent que les voisins ACTIFS (entendus depuis
        `now - active_window`) — le filtre s'applique à l'extraction, donc `count`/`best_snr`,
        les voisins stockés (donc `distinct_*`) ET `max_distance_km` excluent les périmés.
        """
        info = self._iface.getMyNodeInfo() or {}
        # Statut MQTT (onboarding) : lu de la config LOCALE du node (pas d'I/O radio).
        module_config = getattr(getattr(self._iface, "localNode", None), "moduleConfig", None)
        mqtt = metrics.mqtt_status(getattr(module_config, "mqtt", None))
        node = dict(metrics.node_metrics(info), **metrics.node_identity(info), **mqtt)
        nodes_by_num = getattr(self._iface, "nodesByNum", None) or {}
        pos = metrics.position(info)
        nbrs = metrics.neighbors(nodes_by_num, info.get("num"), now=now, active_window=active_window)
        # Portée : distance du voisin 0-hop ACTIF le plus lointain (haversine passerelle↔voisins,
        # calcul LOCAL). Stockée sur node_metrics (pattern des colonnes mqtt_*).
        node["max_distance_km"] = metrics.max_distance_km(pos, nbrs)
        return {
            "node": node,
            "position": pos,
            "neighbors": nbrs,
        }

    def send(self, command: dict) -> dict:
        """Exécute une commande downlink (write BLE). Voir `control`. Suit l'ACK si demandé."""
        result = self._executor(self._iface, command)
        packet_id = result.get("packet_id")
        if command.get("want_ack") and packet_id is not None:
            self._track_ack(packet_id, f"canal={command.get('channel')}")
        return result

    def _track_ack(self, packet_id, label: str) -> None:
        """Arme l'attente d'un ACK radio (ROUTING_APP entrant) + un timeout de repli."""
        timer = self._timer_factory(ACK_TIMEOUT, lambda: self._ack_timeout(packet_id, label))
        timer.daemon = True
        with self._ack_lock:
            self._pending_acks[packet_id] = (label, timer)
        timer.start()

    def _ack_timeout(self, packet_id, label: str) -> None:
        with self._ack_lock:
            present = self._pending_acks.pop(packet_id, None)
        if present is not None:
            log.info("[downlink] ACK %s → timeout (aucun accusé reçu)", label)

    def _handler_receive(self, packet=None, interface=None) -> None:
        """ROUTING_APP dont le requestId matche un want_ack -> accusé radio logué."""
        decoded = (packet or {}).get("decoded") or {}
        if decoded.get("portnum") != ROUTING_PORTNUM:
            return
        with self._ack_lock:
            entry = self._pending_acks.pop(decoded.get("requestId"), None)
        if entry is None:
            return
        label, timer = entry
        timer.cancel()
        log.info("[downlink] ACK %s → %s", label, _ack_status(packet))

    def open(self) -> None:
        self._subscribe(self._handler, PROXY_TOPIC)
        self._subscribe(self._handler_lost, CONNECTION_LOST_TOPIC)
        self._subscribe(self._handler_receive, RECEIVE_TOPIC)
        self._iface = self._interface_factory(self._address)
        log.info("node connecté (BLE %s)", self._address)

    def close(self) -> None:
        self._unsubscribe(self._handler, PROXY_TOPIC)
        self._unsubscribe(self._handler_lost, CONNECTION_LOST_TOPIC)
        self._unsubscribe(self._handler_receive, RECEIVE_TOPIC)
        with self._ack_lock:
            for _label, timer in self._pending_acks.values():
                timer.cancel()
            self._pending_acks.clear()
        if self._iface is not None:
            self._iface.close()
            self._iface = None
