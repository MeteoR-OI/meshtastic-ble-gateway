# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Mickaël Hoareau — MeteoR-OI
"""Adaptateur MQTT (paho) — couche fine, injectable pour les tests."""
from __future__ import annotations

import logging
from typing import Callable, Optional

log = logging.getLogger("mbg.mqtt")


def default_client_factory():
    """Crée un client paho compatible 1.x et 2.x (API callback versionnée en 2.0)."""
    import paho.mqtt.client as mqtt

    try:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    except AttributeError:  # paho < 2.0
        return mqtt.Client()


class PahoPublisher:
    """Publie sur un broker MQTT. `client_factory` injectable pour les tests."""

    def __init__(
        self,
        host: str,
        port: int = 1883,
        username: Optional[str] = None,
        password: Optional[str] = None,
        *,
        client_factory: Callable[[], object] = default_client_factory,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._client_factory = client_factory
        self._client = None

    def connect(self) -> None:
        client = self._client_factory()
        if self._username:
            client.username_pw_set(self._username, self._password)
        client.connect(self._host, self._port, keepalive=60)
        client.loop_start()
        self._client = client
        log.info("broker connecté %s:%s", self._host, self._port)

    def publish(self, topic: str, payload: bytes) -> None:
        if self._client is None:
            raise RuntimeError("publisher non connecté")
        self._client.publish(topic, payload)

    def close(self) -> None:
        if self._client is None:
            return
        self._client.loop_stop()
        self._client.disconnect()
        self._client = None
