# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Mickaël Hoareau — MeteoR-OI
"""Point d'entrée CLI de la passerelle."""
from __future__ import annotations

import argparse
import logging
import signal
from typing import Optional, Sequence

from .config import Config
from .mqtt_publisher import PahoPublisher
from .node import MeshtasticNodeLink
from .runner import Gateway

log = logging.getLogger("mbg")


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
        poll_interval=env_config.poll_interval,
    )

    def publisher_factory():
        return PahoPublisher(
            config.broker_host, config.broker_port, config.broker_username, config.broker_password
        )

    def nodelink_factory(address, on_proxy, on_lost):
        return MeshtasticNodeLink(address, on_proxy, on_lost)

    gateway = Gateway(config, publisher_factory, nodelink_factory)

    stop = {"flag": False}

    def _stop(signum, frame):
        stop["flag"] = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    log.info(
        "démarrage passerelle : node=%s broker=%s:%s",
        config.ble_address,
        config.broker_host,
        config.broker_port,
    )
    gateway.run(lambda: not stop["flag"])
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
