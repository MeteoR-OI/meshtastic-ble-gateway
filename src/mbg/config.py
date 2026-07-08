# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Mickaël Hoareau — MeteoR-OI
"""Configuration de la passerelle (défauts + surcharge par variables d'env)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping, Optional

DEFAULT_BLE = "E6:E3:53:4B:BE:A5"  # T114 par défaut (MAC côté BlueZ/RPi)


@dataclass(frozen=True)
class Config:
    """Paramètres immuables de la passerelle."""

    ble_address: str = DEFAULT_BLE
    broker_host: str = "localhost"
    broker_port: int = 1883
    broker_username: Optional[str] = None
    broker_password: Optional[str] = None
    reconnect_delay: float = 5.0  # délai initial de respawn du worker (backoff)
    max_reconnect_delay: float = 30.0  # plafond du backoff exponentiel
    poll_interval: float = 0.5  # granularité + cadence sonde/heartbeat du worker
    supervisor_tick: float = 1.0  # cadence de surveillance du superviseur
    connect_grace: float = 45.0  # délai toléré sans heartbeat pendant la connexion BLE
    alive_timeout: float = 15.0  # gap max entre heartbeats une fois le worker connecté
    # API de contrôle (downlink). Token vide => API désactivée (fermé par défaut).
    api_token: Optional[str] = None
    api_host: str = "0.0.0.0"
    api_port: int = 8080
    control_timeout: float = 10.0  # attente max d'une réponse worker à une commande

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "Config":
        """Construit une Config depuis l'environnement (préfixe MBG_)."""
        src = os.environ if env is None else env
        return cls(
            ble_address=src.get("MBG_BLE_ADDRESS", DEFAULT_BLE),
            broker_host=src.get("MBG_BROKER_HOST", "localhost"),
            broker_port=int(src.get("MBG_BROKER_PORT", "1883")),
            broker_username=src.get("MBG_BROKER_USERNAME") or None,
            broker_password=src.get("MBG_BROKER_PASSWORD") or None,
            reconnect_delay=float(src.get("MBG_RECONNECT_DELAY", "5")),
            max_reconnect_delay=float(src.get("MBG_MAX_RECONNECT_DELAY", "30")),
            poll_interval=float(src.get("MBG_POLL_INTERVAL", "0.5")),
            supervisor_tick=float(src.get("MBG_SUPERVISOR_TICK", "1")),
            connect_grace=float(src.get("MBG_CONNECT_GRACE", "45")),
            alive_timeout=float(src.get("MBG_ALIVE_TIMEOUT", "15")),
            api_token=src.get("MBG_API_TOKEN") or None,
            api_host=src.get("MBG_API_HOST", "0.0.0.0"),
            api_port=int(src.get("MBG_API_PORT", "8080")),
            control_timeout=float(src.get("MBG_CONTROL_TIMEOUT", "10")),
        )
