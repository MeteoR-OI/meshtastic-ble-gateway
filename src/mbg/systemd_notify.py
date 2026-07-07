# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Mickaël Hoareau — MeteoR-OI
"""Notification systemd (sd_notify) sans dépendance.

Permet à systemd de surveiller la vivacité du process via `WatchdogSec` :
l'app envoie `WATCHDOG=1` à chaque cycle sain ; si systemd cesse de les
recevoir, il relance le service (filet ultime contre un gel total).
No-op silencieux hors systemd (variable NOTIFY_SOCKET absente).
"""
from __future__ import annotations

import os
import socket
from typing import Callable, Mapping, Optional


def sd_notify(
    state: str,
    *,
    env: Optional[Mapping[str, str]] = None,
    socket_factory: Callable[..., socket.socket] = socket.socket,
) -> bool:
    """Envoie un message d'état à systemd. Renvoie True si transmis, False sinon."""
    addr = (os.environ if env is None else env).get("NOTIFY_SOCKET")
    if not addr:
        return False
    # Namespace abstrait Linux : '@' -> octet nul en tête.
    path = "\0" + addr[1:] if addr.startswith("@") else addr
    try:
        sock = socket_factory(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            sock.connect(path)
            sock.sendall(state.encode())
        finally:
            sock.close()
        return True
    except OSError:
        return False
