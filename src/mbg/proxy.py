# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Mickaël Hoareau — MeteoR-OI
"""Cœur du relais : republie chaque message Client Proxy vers le broker, tel quel.

Aucune connaissance du contenu : le node envoie un `MqttClientProxyMessage`
(topic MQTT complet + payload), on le republie à l'identique. Le déchiffrement
et le parsing vivent côté MeshForge, pas ici.
"""
from __future__ import annotations

import logging
from typing import Optional, Protocol, Tuple

log = logging.getLogger("mbg.proxy")


def _envelope_header(data: bytes) -> Tuple[Optional[str], Optional[int]]:
    """(!fromId, id) lus dans l'en-tête ServiceEnvelope (en CLAIR, sans clé) — pour le log.

    Best-effort : ne déchiffre RIEN, ne modifie RIEN (le forward reste opaque). Renvoie
    (None, None) si le payload n'est pas un ServiceEnvelope décodable (ex. JSON, map…).
    """
    try:
        from meshtastic.protobuf import mqtt_pb2

        env = mqtt_pb2.ServiceEnvelope()
        env.ParseFromString(data)
        src = getattr(env.packet, "from")
        return "!%08x" % (src & 0xFFFFFFFF), env.packet.id
    except Exception:  # noqa: BLE001 — décodage best-effort, jamais bloquant
        return None, None


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
        src, pid = _envelope_header(proxymessage.data)
        if src is not None:
            log.info(
                "[uplink] from=%s id=%s %s (%d octets)",
                src, pid, proxymessage.topic, len(proxymessage.data),
            )
        else:
            log.info("[uplink] %s (%d octets)", proxymessage.topic, len(proxymessage.data))
