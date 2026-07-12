# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Mickaël Hoareau — MeteoR-OI
"""Extraction des métriques du node depuis meshtastic (getMyNodeInfo / nodesByNum).

Lecture ACTIVE locale : `getMyNodeInfo()['deviceMetrics']` donne la batterie fraîche
sans dépendre du broadcast (deviceUpdateInterval = 12 h). Fonctions pures (dicts en
entrée) — testables sans matériel.

NB : pas de RSSI du lien BLE (RPi↔node). Vérifié sur MHA235 (BlueZ 5.55) : `bluetoothd`
détient le contrôleur, donc ni HCI `Read RSSI` (`hcitool rssi`), ni mgmt `Get Conn Info`
(`btmgmt conn-info`), ni D-Bus `Device1.RSSI` ne renvoient de valeur pour un lien LE
connecté — même en root — sauf à détacher le contrôleur (= couper la passerelle). Le
signal de qualité du lien BLE est donc le compteur de reconnexions (`link_quality`).
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

# Plancher de la fenêtre "voisin actif" (V0.8.2). Découplé de la cadence d'ÉMISSION du node
# (position_broadcast défaut 900 s) qu'on ne connaît pas : 1 h tolère plusieurs diffusions
# manquées tout en excluant le vrai périmé (entendu il y a des heures/jours).
NEIGHBOR_ACTIVE_FLOOR = 3600.0


def resolve_active_window(monitor_interval: float, override: float = 0.0) -> float:
    """Fenêtre 'actif' effective (s). `override>0` prime ; sinon max(monitor_interval, plancher).

    Borne basse = `monitor_interval` pour qu'un échantillonnage lent (paliers batterie) couvre
    au moins un cycle de sonde ; plancher = `NEIGHBOR_ACTIVE_FLOOR` pour qu'un échantillonnage
    rapide (défaut 300 s) ne fasse pas flapper les voisins à diffusion peu fréquente.
    """
    if override > 0:
        return override
    return max(monitor_interval, NEIGHBOR_ACTIVE_FLOOR)


def node_metrics(info: Dict[str, Any]) -> Dict[str, Any]:
    """Device metrics depuis le dict getMyNodeInfo()."""
    dm = info.get("deviceMetrics") or {}
    return {
        "battery_level": dm.get("batteryLevel"),
        "voltage": dm.get("voltage"),
        "channel_util": dm.get("channelUtilization"),
        "air_util_tx": dm.get("airUtilTx"),
        "uptime": dm.get("uptimeSeconds"),
    }


def node_identity(info: Dict[str, Any]) -> Dict[str, Any]:
    """Identité du node local (id + nom humain) depuis `getMyNodeInfo()['user']`."""
    user = info.get("user") or {}
    return {
        "node_id": user.get("id"),
        "node_name": user.get("longName") or user.get("shortName"),
    }


def mqtt_status(mqtt_config: Any) -> Dict[str, Any]:
    """Statut MQTT du node depuis `localNode.moduleConfig.mqtt` (onboarding, CONTRACTS §3).

    `mqtt_proxy_ok` = module MQTT activé ET proxy client activé — c'est la paire qui
    fait remonter le trafic du node via la passerelle. Fail-soft : config absente
    (localNode pas encore chargé, fake incomplet) -> tous les champs à None.
    """
    if mqtt_config is None:
        return {"mqtt_broker": None, "mqtt_proxy_ok": None, "mqtt_map_reporting": None}
    return {
        "mqtt_broker": getattr(mqtt_config, "address", None) or None,
        "mqtt_proxy_ok": bool(getattr(mqtt_config, "enabled", False))
        and bool(getattr(mqtt_config, "proxy_to_client_enabled", False)),
        "mqtt_map_reporting": bool(getattr(mqtt_config, "map_reporting_enabled", False)),
    }


def position(info: Dict[str, Any]) -> Dict[str, Any]:
    pos = info.get("position") or {}
    return {"lat": pos.get("latitude"), "lon": pos.get("longitude"), "altitude": pos.get("altitude")}


def neighbors(
    nodes_by_num: Dict[int, Any],
    my_num: Optional[int],
    *,
    now: Optional[float] = None,
    active_window: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Voisins directs (0-hop) **actifs**, avec SNR/RSSI + position (si connue).

    `lat`/`lon` sont lus LOCALEMENT dans le dict NodeDB déjà en main (aucune op BLE) ;
    ils servent au calcul de `max_distance_km`. None si le voisin n'a jamais diffusé
    sa position.

    Filtre d'ACTIVITÉ (V0.8.2) : si `now` ET `active_window` sont fournis, on ne garde que
    les voisins entendus depuis `now - active_window` — la NodeDB accumule des nodes entendus
    il y a longtemps dont la position PÉRIMÉE gonflerait `max_distance`. Un voisin sans
    `lastHeard` ne peut pas prouver sa fraîcheur → exclu quand le filtre est actif. Sans les
    deux paramètres : pas de filtre temporel (compat).
    """
    filtering = now is not None and active_window is not None
    cutoff = (now - active_window) if filtering else None
    out: List[Dict[str, Any]] = []
    for num, node in (nodes_by_num or {}).items():
        if num == my_num:
            continue
        if node.get("hopsAway") != 0:  # 0-hop = portée radio directe
            continue
        last_heard = node.get("lastHeard")
        if filtering and (last_heard is None or last_heard < cutoff):
            continue  # périmé (ou fraîcheur inconnue) -> pas un voisin actif
        user = node.get("user") or {}
        pos = node.get("position") or {}
        out.append(
            {
                "node_id": user.get("id") or ("!%08x" % (num & 0xFFFFFFFF)),
                "snr": node.get("snr"),
                "rssi": node.get("rssi"),
                "last_heard": last_heard,
                "lat": pos.get("latitude"),
                "lon": pos.get("longitude"),
            }
        )
    return out


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance orthodromique (km) entre deux points WGS84."""
    r = 6371.0088  # rayon terrestre moyen (km)
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def max_distance_km(gateway: Dict[str, Any], neighbor_list: List[Dict[str, Any]]) -> Optional[float]:
    """Distance (km) du voisin 0-hop le plus lointain dont on connaît la position.

    Haversine entre la position de la passerelle (`gateway["lat"]/["lon"]`, cf.
    `position()`) et chaque voisin. None si la passerelle n'a pas de position, ou si
    aucun voisin n'en a. Arrondi à 0,1 km. Purement local (aucune op BLE).
    """
    glat, glon = gateway.get("lat"), gateway.get("lon")
    if glat is None or glon is None:
        return None
    distances = [
        _haversine_km(glat, glon, n["lat"], n["lon"])
        for n in neighbor_list
        if n.get("lat") is not None and n.get("lon") is not None
    ]
    if not distances:
        return None
    return round(max(distances), 1)
