# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Mickaël Hoareau — MeteoR-OI
"""Configuration de la passerelle (défauts + surcharge par variables d'env)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping, Optional, Tuple

DEFAULT_BLE = "E6:E3:53:4B:BE:A5"  # T114 par défaut (MAC côté BlueZ/RPi)


def _csv_tuple(raw: Optional[str]) -> Tuple[str, ...]:
    """Découpe une liste `a,b,c` d'env en tuple (immutable → hashable pour le dataclass frozen)."""
    if not raw:
        return ()
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _is_true(raw: Optional[str]) -> bool:
    return (raw or "").lower() in ("1", "true", "yes")


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
    # Monitoring / sonde (V0.3).
    db_path: str = "metrics.db"  # base SQLite (relative au WorkingDirectory du service)
    monitor_interval: float = 300.0  # cadence de relevé des métriques node (s ; 0 = off)
    force_telemetry: bool = False  # envoyer sendTelemetry avant le relevé (mesure fraîche)
    # Fenêtre "voisin actif" (V0.8.2) : un voisin 0-hop ne compte que si entendu depuis
    # `now - W`. 0 = auto = max(monitor_interval, NEIGHBOR_ACTIVE_FLOOR) ; sinon override en s.
    neighbor_active_secs: float = 0.0
    dump_dir: Optional[str] = None  # répertoire d'export CSV (None = pas d'export)
    dump_interval: float = 3600.0  # cadence d'export CSV + purge (s)
    retention_days: float = 0.0  # purge au-delà de N jours (0 = pas de purge)
    # Paliers batterie + duty-cycle (V0.4). Opt-in ; nécessite le monitoring (source batterie).
    battery_tiers: bool = False  # active la cadence adaptative + le duty-cycle < 25 %
    duty_on: float = 300.0  # palier critique : durée de la fenêtre de connexion (s)
    duty_off: float = 1800.0  # palier critique : durée de déconnexion entre fenêtres (s)
    tier_hysteresis: float = 3.0  # marge (%) anti-flapping entre paliers
    # Stabilisation du lien BLE (V0.5). Opt-in ; nécessite CAP_NET_ADMIN sur le service.
    # 0 = off. >0 = supervision timeout (ms) imposé au lien via `hcitool lecup` à chaque
    # session (contourne le bug BlueZ #717 sur lien faible ; cf. link_tuner).
    ble_supervision_timeout_ms: int = 0
    # Traceroute (endpoint /traceroute + planificateur auto). Cf. traceroute.py / traceroute_scheduler.py.
    # L'ENDPOINT est dispo dès que l'API a un token (coordinateur monté dans le worker) ;
    # le PLANIFICATEUR est opt-in via `traceroute_enabled` (défaut off). Parcimonie airtime :
    # défauts conservateurs (quelques traceroute/jour), très en deçà du rate-limit firmware.
    traceroute_enabled: bool = False  # active le planificateur automatique (opt-in)
    traceroute_policy: str = "staleness"  # static | staleness (recent|adaptive à venir)
    traceroute_daily_budget: int = 6  # nb max de traceroute auto / jour (fenêtre active)
    traceroute_hop_limit: int = 7  # hop_limit par défaut (borné [1..7])
    traceroute_targets: Tuple[str, ...] = ()  # cibles !hex (requis si policy=static)
    traceroute_recent_h: float = 24.0  # policy=staleness : fenêtre "entendu récemment" (h)
    traceroute_per_node_min_s: float = 21600.0  # intervalle min par nœud (défaut 6 h)
    traceroute_min_gap_s: float = 900.0  # intervalle min global entre 2 traceroute (défaut 15 min)
    traceroute_quiet_hours: str = "22:00-06:00"  # plage sans émission (TZ station ; "" = aucune)
    traceroute_max_chanutil: float = 40.0  # skip le tick si channel_utilization local dépasse (%)
    traceroute_priority: Tuple[str, ...] = ()  # policy=staleness : nœuds prioritaires (facultatif)
    traceroute_tick_s: float = 300.0  # période d'évaluation du planificateur (s)
    traceroute_topic: str = "mbg/traceroute"  # topic MQTT de publication du résultat

    @property
    def traceroute_active(self) -> bool:
        """Vrai s'il faut monter le coordinateur/le store traceroute : planificateur opt-in
        activé, OU API ouverte (l'endpoint /traceroute doit répondre)."""
        return self.traceroute_enabled or bool(self.api_token)

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
            db_path=src.get("MBG_DB_PATH", "metrics.db"),
            monitor_interval=float(src.get("MBG_MONITOR_INTERVAL", "300")),
            force_telemetry=src.get("MBG_MONITOR_FORCE_TELEMETRY", "").lower() in ("1", "true", "yes"),
            neighbor_active_secs=float(src.get("MBG_NEIGHBOR_ACTIVE_SECS", "0")),
            dump_dir=src.get("MBG_DUMP_DIR") or None,
            dump_interval=float(src.get("MBG_DUMP_INTERVAL", "3600")),
            retention_days=float(src.get("MBG_RETENTION_DAYS", "0")),
            battery_tiers=src.get("MBG_BATTERY_TIERS", "").lower() in ("1", "true", "yes"),
            duty_on=float(src.get("MBG_DUTY_ON", "300")),
            duty_off=float(src.get("MBG_DUTY_OFF", "1800")),
            tier_hysteresis=float(src.get("MBG_TIER_HYSTERESIS", "3")),
            ble_supervision_timeout_ms=int(src.get("MBG_BLE_SUPERVISION_TIMEOUT_MS", "0")),
            traceroute_enabled=_is_true(src.get("MBG_TRACEROUTE_ENABLED")),
            traceroute_policy=src.get("MBG_TRACEROUTE_POLICY", "staleness"),
            traceroute_daily_budget=int(src.get("MBG_TRACEROUTE_DAILY_BUDGET", "6")),
            traceroute_hop_limit=int(src.get("MBG_TRACEROUTE_HOP_LIMIT", "7")),
            traceroute_targets=_csv_tuple(src.get("MBG_TRACEROUTE_TARGETS")),
            traceroute_recent_h=float(src.get("MBG_TRACEROUTE_RECENT_H", "24")),
            traceroute_per_node_min_s=float(src.get("MBG_TRACEROUTE_PER_NODE_MIN_S", "21600")),
            traceroute_min_gap_s=float(src.get("MBG_TRACEROUTE_MIN_GAP_S", "900")),
            traceroute_quiet_hours=src.get("MBG_TRACEROUTE_QUIET_HOURS", "22:00-06:00"),
            traceroute_max_chanutil=float(src.get("MBG_TRACEROUTE_MAX_CHANUTIL", "40")),
            traceroute_priority=_csv_tuple(src.get("MBG_TRACEROUTE_PRIORITY")),
            traceroute_tick_s=float(src.get("MBG_TRACEROUTE_TICK_S", "300")),
            traceroute_topic=src.get("MBG_TRACEROUTE_TOPIC", "mbg/traceroute"),
        )
