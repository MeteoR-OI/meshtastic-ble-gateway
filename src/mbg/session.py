# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Mickaël Hoareau — MeteoR-OI
"""Une session BLE↔broker, exécutée dans le worker (sous-processus jetable).

Tourne jusqu'au premier décrochage BLE, puis rend la main : le worker fait alors
`os._exit` (le superviseur respawn). On ne ferme volontairement PAS l'interface
ici — le teardown meshtastic gèle sur lien mort, donc on laisse l'OS récupérer
(via os._exit / SIGKILL). Un `heartbeat()` est émis à chaque poll pour que le
superviseur sache que le worker est vivant.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

from .config import Config
from .proxy import Proxy

log = logging.getLogger("mbg.session")

PublisherFactory = Callable[[], object]
NodeLinkFactory = Callable[[str, Callable[[object], None], Callable[[], None]], object]


def run_one_session(
    config: Config,
    publisher_factory: PublisherFactory,
    nodelink_factory: NodeLinkFactory,
    heartbeat: Callable[[], None],
    should_continue: Callable[[], bool],
    *,
    sleep: Callable[[float], None] = time.sleep,
    commands=None,
    monitor: Optional[Callable[[object], None]] = None,
    tune: Optional[Callable[[], None]] = None,
) -> int:
    """Établit broker + BLE, relaie jusqu'au décrochage. Renvoie le nb de paquets relayés.

    `commands` (optionnel) : canal downlink avec `drain()`/`reply(id, result)`. À chaque
    poll, les commandes en attente sont exécutées via `link.send()` (write BLE). Un write
    qui gèle bloque le poll → plus de heartbeat → le superviseur SIGKILL le worker.

    `tune` (optionnel, V0.5) : appelé UNE fois dès le lien établi pour stabiliser le BLE
    (supervision timeout via `hcitool lecup`). Ne lève jamais (voir link_tuner).
    """
    publisher = publisher_factory()
    publisher.connect()
    proxy = Proxy(publisher)
    lost = threading.Event()  # armé si meshtastic émet connection.lost
    link = nodelink_factory(config.ble_address, proxy.on_proxy_message, lost.set)
    link.open()
    if tune is not None:
        tune()  # une fois par session : impose le supervision timeout sur le lien vivant
    # Cadence de monitoring exprimée en nombre de polls (0 = monitoring désactivé).
    polls_per_monitor = (
        max(1, round(config.monitor_interval / config.poll_interval))
        if monitor is not None and config.monitor_interval > 0
        else 0
    )
    poll_count = 0
    sampled_once = False  # a-t-on déjà relevé les métriques dans cette session ?
    while should_continue():
        # Coupure signalée (lost) OU silencieuse (sonde de vivacité) -> on rend la main.
        if lost.is_set() or not link.is_alive():
            log.warning("lien BLE perdu (%d relayés)", proxy.forwarded)
            break
        if commands is not None:
            for cmd in commands.drain():
                commands.reply(cmd["id"], link.send(cmd))
        if polls_per_monitor:
            # Relève une fois TÔT dans la session (dès le lien établi) puis à la cadence
            # monitor_interval. Sinon, si la session BLE meurt avant le 1er tic périodique
            # (lien instable : sessions < monitor_interval), on ne capturerait JAMAIS de
            # node_metrics. Le compteur repart à zéro à chaque session/respawn.
            if not sampled_once or poll_count >= polls_per_monitor:
                poll_count = 0
                sampled_once = True
                monitor(link)
            poll_count += 1
        heartbeat()
        sleep(config.poll_interval)
    log.info("session terminée (%d relayés, %d erreurs)", proxy.forwarded, proxy.errors)
    return proxy.forwarded
