# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Mickaël Hoareau — MeteoR-OI
"""Point d'entrée CLI de la passerelle."""
from __future__ import annotations

import argparse
import logging
import multiprocessing
import signal
from typing import Optional, Sequence

from . import api
from .config import Config
from .process_backend import spawn_worker
from .supervisor import Supervisor

log = logging.getLogger("mbg")


def _build_serve(config: Config):
    """Renvoie un `serve(submit, should_run)` pour l'API, ou None si pas de token."""
    if not config.api_token:
        return None

    def serve(submit, should_run):
        api.serve(
            config.api_host, config.api_port, config.api_token,
            config.control_timeout, submit, should_run,
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
    config = Config(
        ble_address=args.ble,
        broker_host=args.broker,
        broker_port=args.port,
        broker_username=args.username,
        broker_password=args.password,
        reconnect_delay=env_config.reconnect_delay,
        max_reconnect_delay=env_config.max_reconnect_delay,
        poll_interval=env_config.poll_interval,
        supervisor_tick=env_config.supervisor_tick,
        connect_grace=env_config.connect_grace,
        alive_timeout=env_config.alive_timeout,
        api_token=env_config.api_token,
        api_host=env_config.api_host,
        api_port=env_config.api_port,
        control_timeout=env_config.control_timeout,
    )

    # Le BLE tourne dans un sous-processus jetable ; le superviseur (ce process) ne
    # touche jamais au BLE, donc ne fige jamais. L'API de contrôle (si token) tourne
    # dans un thread du superviseur.
    ctx = multiprocessing.get_context("fork")
    supervisor = Supervisor(
        config, lambda: spawn_worker(config, ctx), serve=_build_serve(config)
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
