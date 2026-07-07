# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Mickaël Hoareau — MeteoR-OI
"""Anti-gel BLE : neutralise `MeshInterface._sendDisconnect` de meshtastic.

Cause racine (confirmée par py-spy en prod) : `MeshInterface.close()` appelle
`_sendDisconnect()`, qui écrit un paquet « disconnect » au radio
(`write_gatt_char`, response=True, SANS timeout). Sur un lien BLE déjà mort cette
écriture bloque indéfiniment (`wait()`), gelant à la fois notre `close()` (thread
principal) ET le callback de déconnexion interne de meshtastic (thread asyncio).
Résultat : la reconnexion in-process ne va jamais au bout, seul le watchdog
systemd récupère (restart complet).

La passerelle ne fait que RECEVOIR (aucune écriture radio en fonctionnement),
donc ce paquet « disconnect » est inutile. On le rend inopérant → `close()` se
termine → la reconnexion in-process aboutit.

Patch défensif : idempotent, et no-op silencieux si l'API interne de meshtastic a
changé (auquel cas le watchdog systemd reste le filet ultime).
"""
from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger("mbg.patch")

_PATCH_FLAG = "_mbg_disconnect_neutralized"


def apply_meshtastic_patches(interface_cls: Optional[type] = None) -> bool:
    """Neutralise `_sendDisconnect`. Renvoie True si le patch est en place."""
    if interface_cls is None:
        try:
            from meshtastic.mesh_interface import MeshInterface

            interface_cls = MeshInterface
        except Exception as exc:  # noqa: BLE001 — meshtastic absent/inattendu : on n'échoue pas
            log.warning("anti-gel BLE non appliqué (import meshtastic) : %s", exc)
            return False

    if getattr(interface_cls, _PATCH_FLAG, False):
        return True  # déjà patché (idempotent)

    if not hasattr(interface_cls, "_sendDisconnect"):
        log.warning(
            "anti-gel BLE non appliqué : _sendDisconnect introuvable (API meshtastic "
            "changée ?) — le watchdog systemd reste le filet"
        )
        return False

    def _noop_send_disconnect(self) -> None:
        # Receive-only : le paquet disconnect est inutile et gèle sur lien mort.
        return None

    interface_cls._sendDisconnect = _noop_send_disconnect
    setattr(interface_cls, _PATCH_FLAG, True)
    log.info("anti-gel BLE appliqué : meshtastic._sendDisconnect neutralisé")
    return True
