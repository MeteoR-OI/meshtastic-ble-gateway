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
    reconnect_delay: float = 5.0  # délai initial entre deux tentatives (backoff)
    max_reconnect_delay: float = 30.0  # plafond du backoff exponentiel
    poll_interval: float = 0.5  # granularité + cadence de la sonde de vivacité

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
        )
