# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Mickaël Hoareau — MeteoR-OI
"""Traduction d'une commande (dict) en action meshtastic sur l'interface BLE.

Exécuté DANS le worker (il détient l'`iface`). Une commande = envoi de texte,
envoi de télémétrie, ou admin (réglage curaté du node). Ne lève JAMAIS : toute
erreur devient `{"ok": False, "error": ...}` pour ne pas tuer le worker. NB : un
write sur lien mort GÈLE (pas une exception) → le worker cesse de battre → le
superviseur le SIGKILL (comportement voulu de l'isolation).
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Tuple


def _coerce_int(value: Any) -> int:
    return int(value)


def _coerce_role(value: Any) -> int:
    """Rôle device : accepte un entier ou un nom (ex. "ROUTER")."""
    if isinstance(value, int) or (isinstance(value, str) and value.lstrip("-").isdigit()):
        return int(value)
    from meshtastic import config_pb2

    return config_pb2.Config.DeviceConfig.Role.Value(str(value).upper())


def _coerce_gps_mode(value: Any) -> int:
    if isinstance(value, int) or (isinstance(value, str) and value.lstrip("-").isdigit()):
        return int(value)
    from meshtastic import config_pb2

    return config_pb2.Config.PositionConfig.GpsMode.Value(str(value).upper())


# Réglages admin autorisés : nom -> (attribut config, section, champ, coercion).
# Extensible : ajouter une entrée suffit.
ADMIN_SETTINGS: Dict[str, Tuple[str, str, str, Callable[[Any], Any]]] = {
    "role": ("localConfig", "device", "role", _coerce_role),
    "position_broadcast_secs": ("localConfig", "position", "position_broadcast_secs", _coerce_int),
    "gps_mode": ("localConfig", "position", "gps_mode", _coerce_gps_mode),
    "device_update_interval": ("moduleConfig", "telemetry", "device_update_interval", _coerce_int),
}


def _resolve_channel(iface, channel: Any) -> int:
    """Résout un canal en index : entier direct, ou nom via la config du node."""
    if isinstance(channel, int):
        return channel
    if isinstance(channel, str) and channel.isdigit():
        return int(channel)
    for ch in iface.localNode.channels:
        if ch.settings.name == channel:
            return ch.index
    raise ValueError(f"canal inconnu: {channel}")


def _send_text(iface, command: Dict[str, Any]) -> Dict[str, Any]:
    text = command.get("text")
    if not text:
        return {"ok": False, "error": "texte manquant"}
    channel_index = _resolve_channel(iface, command.get("channel", 0))
    kwargs: Dict[str, Any] = {"channelIndex": channel_index}
    dest = command.get("dest")
    if dest:
        kwargs["destinationId"] = dest
    iface.sendText(text, **kwargs)
    return {"ok": True, "detail": f"texte envoyé (canal {channel_index})"}


def _apply_admin(iface, setting: Any, value: Any) -> Dict[str, Any]:
    spec = ADMIN_SETTINGS.get(setting)
    if spec is None:
        return {"ok": False, "error": f"réglage admin inconnu: {setting}"}
    config_attr, section, field, coerce = spec
    coerced = coerce(value)
    node = iface.localNode
    section_obj = getattr(getattr(node, config_attr), section)
    setattr(section_obj, field, coerced)
    node.writeConfig(section)
    return {"ok": True, "detail": f"{setting}={coerced}"}


def execute_command(iface, command: Dict[str, Any]) -> Dict[str, Any]:
    """Exécute une commande. Ne lève jamais (erreur -> dict {ok: False, error})."""
    try:
        ctype = command.get("type")
        if ctype == "text":
            return _send_text(iface, command)
        if ctype == "telemetry":
            iface.sendTelemetry()
            return {"ok": True, "detail": "télémétrie envoyée"}
        if ctype == "admin":
            return _apply_admin(iface, command.get("setting"), command.get("value"))
        return {"ok": False, "error": f"type de commande inconnu: {ctype}"}
    except Exception as exc:  # noqa: BLE001 — jamais tuer le worker sur une commande
        return {"ok": False, "error": str(exc)}
