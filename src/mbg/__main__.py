# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Mickaël Hoareau — MeteoR-OI
"""Point d'entrée CLI de la passerelle."""
from __future__ import annotations

import argparse
import logging
import multiprocessing
import signal
from dataclasses import replace
from typing import Optional, Sequence

from . import __version__, api, metrics
from .config import Config
from .process_backend import spawn_worker
from .storage import MetricsStore
from .supervisor import Supervisor

log = logging.getLogger("mbg")


def _build_serve(config: Config, metrics):
    """Renvoie un `serve(submit, should_run)` pour l'API, ou None si pas de token."""
    if not config.api_token:
        return None

    # Infos statiques exposées par GET /info (découverte : version + config).
    info = {
        "version": __version__,
        "monitor_interval": config.monitor_interval,
        "battery_tiers": config.battery_tiers,
    }

    def serve(submit, should_run):
        api.serve(
            config.api_host, config.api_port, config.api_token,
            config.control_timeout, submit, metrics, should_run, info,
        )

    return serve


def build_parser(defaults: Config) -> argparse.ArgumentParser:
    """Parser dont les défauts viennent de l'ENV (MBG_*) ; la CLI ne fait qu'override."""
    p = argparse.ArgumentParser(prog="mbg", description="Passerelle BLE → MQTT Meshtastic")
    p.add_argument("--ble", default=defaults.ble_address, help="MAC/nom BLE du node")
    p.add_argument("--broker", default=defaults.broker_host, help="hôte du broker MQTT")
    p.add_argument("--port", type=int, default=defaults.broker_port, help="port MQTT")
    p.add_argument("--username", default=defaults.broker_username, help="user MQTT (optionnel)")
    p.add_argument("--password", default=defaults.broker_password, help="password MQTT (optionnel)")
    p.add_argument("-v", "--verbose", action="store_true", help="logs debug")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    # L'environnement (MBG_*) fournit la base — c'est ainsi que systemd configure le
    # service ; les arguments CLI, s'ils sont fournis, priment (usage manuel / PoC).
    env_config = Config.from_env()
    args = build_parser(env_config).parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    # On part de l'ENV et on n'override QUE les champs pilotables par la CLI. Tout le
    # reste (tuning, API, monitoring, et tout futur champ) se propage automatiquement —
    # évite le bug récurrent « champ oublié dans la reconstruction de Config ».
    config = replace(
        env_config,
        ble_address=args.ble,
        broker_host=args.broker,
        broker_port=args.port,
        broker_username=args.username,
        broker_password=args.password,
    )

    # Paliers batterie (V0.4) : nécessitent le monitoring comme source de batterie.
    if config.battery_tiers and config.monitor_interval <= 0:
        log.warning("MBG_BATTERY_TIERS ignoré : nécessite le monitoring (MBG_MONITOR_INTERVAL>0)")
        config = replace(config, battery_tiers=False)

    # Stabilisation du lien BLE (V0.5) : imposée par le worker via hcitool ; nécessite
    # CAP_NET_ADMIN+CAP_NET_RAW sur le service (sinon l'application échoue et est loguée).
    if config.ble_supervision_timeout_ms > 0:
        log.info(
            "stabilisation lien BLE activée : supervision_timeout=%d ms (nécessite CAP_NET_ADMIN)",
            config.ble_supervision_timeout_ms,
        )

    # Le BLE tourne dans un sous-processus jetable ; le superviseur (ce process) ne
    # touche jamais au BLE, donc ne fige jamais. L'API de contrôle (si token) tourne
    # dans un thread du superviseur.
    ctx = multiprocessing.get_context("fork")
    # Store côté superviseur (record_link + export + lectures API). Le worker a le sien
    # (écriture node_metrics/registre voisins) — même fichier SQLite, mode WAL. On lui donne
    # la fenêtre "voisin actif" effective : latest() en a besoin pour filtrer count/best_snr/
    # max_distance* (l'API l'appelle sans argument).
    store = (
        MetricsStore(
            config.db_path,
            active_window=metrics.resolve_active_window(
                config.monitor_interval, config.neighbor_active_secs
            ),
        )
        if config.monitor_interval > 0
        else None
    )
    supervisor = Supervisor(
        config, lambda cfg: spawn_worker(cfg, ctx), serve=_build_serve(config, store), store=store
    )

    stop = {"flag": False}

    def _stop(signum, frame):
        stop["flag"] = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    log.info(
        "démarrage superviseur : node=%s broker=%s:%s",
        config.ble_address,
        config.broker_host,
        config.broker_port,
    )
    supervisor.run(lambda: not stop["flag"])
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
