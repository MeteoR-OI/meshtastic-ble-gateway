# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Mickaël Hoareau — MeteoR-OI
"""Cœur du relais : republie chaque message Client Proxy vers le broker, tel quel.

Aucune connaissance du contenu : le node envoie un `MqttClientProxyMessage`
(topic MQTT complet + payload), on le republie à l'identique. Le déchiffrement
et le parsing vivent côté MeshForge, pas ici.
"""
from __future__ import annotations

import logging
from typing import Protocol

log = logging.getLogger("mbg.proxy")


class ProxyMessage(Protocol):
    """Ce que fournit meshtastic-python sur `meshtastic.mqttclientproxymessage`."""

    topic: str
    data: bytes


class Publisher(Protocol):
    """Cible de republication (implémentée par PahoPublisher, ou un fake en test)."""

    def publish(self, topic: str, payload: bytes) -> None: ...


class Proxy:
    """Relaie les messages Client Proxy vers un `Publisher`, sans jamais crasher."""

    def __init__(self, publisher: Publisher) -> None:
        self._publisher = publisher
        self.forwarded = 0
        self.errors = 0

    def on_proxy_message(self, proxymessage: ProxyMessage) -> None:
        """Callback branché sur le pubsub meshtastic (un message = un uplink)."""
        try:
            self._publisher.publish(proxymessage.topic, proxymessage.data)
        except Exception as exc:  # noqa: BLE001 — un échec broker ne doit pas tuer le callback
            self.errors += 1
            log.warning("échec publish %s: %s", proxymessage.topic, exc)
            return
        self.forwarded += 1
        log.info("[uplink] %s (%d octets)", proxymessage.topic, len(proxymessage.data))
