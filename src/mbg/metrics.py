# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Mickaël Hoareau — MeteoR-OI
"""Extraction des métriques du node depuis meshtastic (getMyNodeInfo / nodesByNum).

Lecture ACTIVE locale : `getMyNodeInfo()['deviceMetrics']` donne la batterie fraîche
sans dépendre du broadcast (deviceUpdateInterval = 12 h). Fonctions pures (dicts en
entrée) sauf `ble_rssi` qui sonde l'interface — testables sans matériel.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


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


def position(info: Dict[str, Any]) -> Dict[str, Any]:
    pos = info.get("position") or {}
    return {"lat": pos.get("latitude"), "lon": pos.get("longitude"), "altitude": pos.get("altitude")}


def neighbors(nodes_by_num: Dict[int, Any], my_num: Optional[int]) -> List[Dict[str, Any]]:
    """Voisins directs (0-hop) entendus, avec SNR/RSSI."""
    out: List[Dict[str, Any]] = []
    for num, node in (nodes_by_num or {}).items():
        if num == my_num:
            continue
        if node.get("hopsAway") != 0:  # 0-hop = portée radio directe
            continue
        user = node.get("user") or {}
        out.append(
            {
                "node_id": user.get("id") or ("!%08x" % (num & 0xFFFFFFFF)),
                "snr": node.get("snr"),
                "rssi": node.get("rssi"),
                "last_heard": node.get("lastHeard"),
            }
        )
    return out


def ble_rssi(iface: Any) -> Optional[int]:
    """RSSI du lien BLE local (best-effort ; souvent None en connexion via bleak/BlueZ)."""
    try:
        client = getattr(iface, "client", None)
        bleak_client = getattr(client, "bleak_client", None)
        props = getattr(bleak_client, "_properties", None) or {}
        value = props.get("RSSI")
        return int(value) if value is not None else None
    except Exception:  # noqa: BLE001 — best-effort, jamais bloquant
        return None
