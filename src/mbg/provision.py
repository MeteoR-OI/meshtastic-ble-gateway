# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Mickaël Hoareau — MeteoR-OI
"""Outil de provisionnement du node par BLE : `python -m mbg.provision`.

Lit (`--inspect`) ou écrit (`--apply`) la config MQTT + position d'un node Meshtastic
**déjà appairé** (l'appairage OS est du ressort de l'installateur), pour l'onboarding
MeteoR-OI/MeshForge. Interface figée : CONTRACTS-onboarding §2 — sortie = UN objet
JSON sur stdout (les logs vont sur stderr), exit 0 si l'opération a pleinement réussi.

Enseignements Phase 0 (T114 réel, meshtastic 2.7.10 / bleak 1.1.1) :
- connexions BLE flaky (1er connect timeout fréquent) → retry avec backoff obligatoire ;
- le node REBOOTE après `commitSettingsTransaction` → commit fire-and-forget (thread
  daemon + join court, l'appel peut ne jamais rendre la main), puis RECONNEXION pour
  relire et vérifier — et jamais de `close()` sur l'interface pré-reboot (gèle sur
  lien mort, la leçon fondatrice de la passerelle) ;
- regrouper toutes les écritures dans UNE transaction pour minimiser les reboots
  (et ne rien écrire du tout si la config est déjà conforme : zéro reboot) ;
- l'entrée réelle (`cli`) SORT via `os._exit` : les threads non-daemon de bleak/meshtastic
  gèleraient un arrêt normal, surtout sur le chemin exit-2 (hw-test T114 2026-07-11).

À lancer passerelle mbg ARRÊTÉE (BLE = 1 seul client). Le relais MQTT est inchangé.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
from typing import Any, Callable, Dict, Optional, Sequence, Tuple

from . import metrics
from .node import default_interface_factory

log = logging.getLogger("mbg.provision")

# Défauts de la cible (CONTRACTS §1 — mêmes noms/valeurs que MbgConfig côté installateur).
DEFAULT_BROKER_HOST = "mqtt-mt.meteor-oi.re"
DEFAULT_BROKER_PORT = 1883
DEFAULT_ROOT_TOPIC = "msh/EU_868"
DEFAULT_POSITION_PRECISION = 15  # ≈ 729 m (confirmé firmware, Phase 0)
DEFAULT_PUBLISH_INTERVAL_SECS = 3600
DEFAULT_BROADCAST_SECS = 900

# Retry BLE (Phase 0 : 1er connect timeout fréquent) + reboot post-commit.
CONNECT_ATTEMPTS = 4
CONNECT_DELAY = 3.0  # délai initial entre tentatives (backoff x2)
CONNECT_MAX_DELAY = 15.0  # plafond du backoff
# Reconnexion POST-REBOOT : budget PATIENT, séparé du connect initial (contrat §2 amendé,
# hw-test T114 2026-07-11) — le node met ~2 min à ré-annoncer après le reboot du commit.
# ≥ ~10 tentatives / ≥ 150 s de wall-clock (ici : 5+10+20+30×6 = 215 s de sleeps).
REBOOT_WAIT = 120.0  # attente avant la 1re tentative (plancher de ré-annonce observé)
RECONNECT_ATTEMPTS = 10
RECONNECT_DELAY = 5.0
RECONNECT_MAX_DELAY = 30.0
COMMITTED_UNVERIFIED_WARNING = "commit envoyé, vérification impossible (node pas encore ré-annoncé)"
COMMIT_JOIN_TIMEOUT = 5.0  # join court du commit fire-and-forget
CLOSE_JOIN_TIMEOUT = 3.0  # join court du close (jamais bloquant)


class ProvisionError(Exception):
    """Échec de provisionnement (connexion BLE, lecture, écriture)."""


def _parse_bool(value: str) -> bool:
    v = value.strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    raise argparse.ArgumentTypeError(f"booléen invalide: {value}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mbg.provision",
        description="Configuration MQTT + position d'un node Meshtastic par BLE (node déjà appairé)",
    )
    p.add_argument("--mac", required=True, help="adresse BLE du node (déjà appairé, mbg arrêtée)")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--inspect", action="store_true", help="lecture seule de la config du node")
    mode.add_argument("--apply", action="store_true", help="écrit la config cible puis relit pour vérifier")
    p.add_argument("--broker", default=DEFAULT_BROKER_HOST, help="broker MQTT cible")
    p.add_argument("--port", type=int, default=DEFAULT_BROKER_PORT, help="port du broker")
    p.add_argument("--username", default=None, help="user MQTT (absent = conserver celui du node)")
    p.add_argument("--password", default=None, help="password MQTT (absent = conserver celui du node)")
    p.add_argument("--root", default=DEFAULT_ROOT_TOPIC, help="topic racine MQTT")
    p.add_argument("--precision", type=int, default=DEFAULT_POSITION_PRECISION,
                   help="position_precision du map report (15 ≈ 729 m)")
    p.add_argument("--publish-interval", type=int, default=DEFAULT_PUBLISH_INTERVAL_SECS,
                   help="publish_interval_secs du map report")
    p.add_argument("--consent", type=_parse_bool, default=True,
                   help="should_report_location du map report (true/false)")
    p.add_argument("--fixed-position", action="store_true",
                   help="active fixed_position sur le node (absent = ne pas y toucher)")
    p.add_argument("--broadcast-secs", type=int, default=DEFAULT_BROADCAST_SECS,
                   help="position_broadcast_secs")
    return p


def target_address(host: str, port: int) -> str:
    """`moduleConfig.mqtt.address` ne porte que l'hôte au port standard, sinon host:port."""
    return host if port == DEFAULT_BROKER_PORT else f"{host}:{port}"


def connect_with_retry(
    address: str,
    *,
    factory: Callable[[str], Any] = default_interface_factory,
    attempts: int = CONNECT_ATTEMPTS,
    delay: float = CONNECT_DELAY,
    max_delay: float = CONNECT_MAX_DELAY,
    sleep: Callable[[float], None] = time.sleep,
) -> Any:
    """Ouvre l'interface BLE avec retry + backoff (connexions flaky, cf. Phase 0)."""
    last: Optional[Exception] = None
    wait = delay
    for attempt in range(1, attempts + 1):
        try:
            return factory(address)
        except Exception as exc:  # noqa: BLE001 — bleak/meshtastic lèvent des types variés
            last = exc
            log.warning("connexion BLE %s : tentative %d/%d échouée (%s)", address, attempt, attempts, exc)
            if attempt < attempts:
                sleep(wait)
                wait = min(wait * 2, max_delay)
    raise ProvisionError(f"connexion BLE impossible ({attempts} tentatives) : {last}")


def read_state(iface: Any) -> Dict[str, Any]:
    """Photographie la config du node au format du contrat (§2)."""
    identity = metrics.node_identity(iface.getMyNodeInfo() or {})
    node = iface.localNode
    mqtt = node.moduleConfig.mqtt
    pos = node.localConfig.position
    return {
        "node_id": identity["node_id"],
        "node_name": identity["node_name"],
        "mqtt": {
            "address": mqtt.address,
            "username": mqtt.username,
            "password": mqtt.password,
            "enabled": bool(mqtt.enabled),
            "proxy_to_client": bool(mqtt.proxy_to_client_enabled),
            "encryption": bool(mqtt.encryption_enabled),
            "json": bool(mqtt.json_enabled),
            "tls": bool(mqtt.tls_enabled),
            "map_reporting": bool(mqtt.map_reporting_enabled),
            "map_report": {
                "publish_interval_secs": mqtt.map_report_settings.publish_interval_secs,
                "position_precision": mqtt.map_report_settings.position_precision,
                "should_report_location": bool(mqtt.map_report_settings.should_report_location),
            },
        },
        "position": {
            "broadcast_secs": pos.position_broadcast_secs,
            "fixed": bool(pos.fixed_position),
        },
    }


def assess(state: Dict[str, Any], expected_address: str) -> Dict[str, Any]:
    """Champs de décision du contrat. La DÉCISION (activer mbg, renvoyer vers register_url)
    reste à l'installateur — ici on fournit les faits."""
    mqtt = state["mqtt"]
    broker_matches = mqtt["address"] == expected_address
    creds_present = bool(mqtt["username"]) and bool(mqtt["password"])
    return {
        "broker_matches": broker_matches,
        "creds_present": creds_present,
        "needs_register": not broker_matches or not creds_present,
    }


def apply_target(
    node: Any,
    args: argparse.Namespace,
    *,
    thread_factory: Callable[..., Any] = threading.Thread,
    commit_timeout: float = COMMIT_JOIN_TIMEOUT,
) -> bool:
    """Écrit la config cible sur le node en UNE transaction (CONTRACTS §2).

    Renvoie True si un commit a été déclenché (=> le node reboote), False si la config
    était déjà conforme (aucune écriture, zéro reboot). Les creds absents de la CLI ne
    sont PAS écrasés (§7.3 : blancs = lus du node). Seules les sections effectivement
    modifiées sont écrites.
    """
    mqtt = node.moduleConfig.mqtt
    before_mqtt = mqtt.SerializeToString()
    mqtt.enabled = True
    mqtt.proxy_to_client_enabled = True
    mqtt.encryption_enabled = True
    mqtt.json_enabled = True
    mqtt.tls_enabled = False
    mqtt.map_reporting_enabled = True
    mqtt.address = target_address(args.broker, args.port)
    mqtt.root = args.root
    if args.username is not None:
        mqtt.username = args.username
    if args.password is not None:
        mqtt.password = args.password
    mqtt.map_report_settings.publish_interval_secs = args.publish_interval
    mqtt.map_report_settings.position_precision = args.precision
    mqtt.map_report_settings.should_report_location = args.consent
    write_mqtt = mqtt.SerializeToString() != before_mqtt

    pos = node.localConfig.position
    before_pos = pos.SerializeToString()
    pos.position_broadcast_secs = args.broadcast_secs
    if args.fixed_position:
        pos.fixed_position = True
    write_pos = pos.SerializeToString() != before_pos

    channel = node.channels[0]  # canal primaire : uplink vers le broker requis
    write_channel = not channel.settings.uplink_enabled
    if write_channel:
        channel.settings.uplink_enabled = True

    if not (write_mqtt or write_pos or write_channel):
        log.info("config déjà conforme — aucune écriture, pas de reboot")
        return False

    node.beginSettingsTransaction()
    if write_mqtt:
        node.writeConfig("mqtt")
    if write_pos:
        node.writeConfig("position")
    if write_channel:
        node.writeChannel(0)
    # Le node REBOOTE en commitant : l'appel peut ne jamais rendre la main (lien qui
    # tombe) → fire-and-forget dans un thread daemon, join court, jamais bloquant.
    committer = thread_factory(target=node.commitSettingsTransaction, daemon=True)
    committer.start()
    committer.join(commit_timeout)
    log.info("transaction commitée (mqtt=%s position=%s canal=%s) — le node reboote",
             write_mqtt, write_pos, write_channel)
    return True


def matches_target(node: Any, args: argparse.Namespace) -> bool:
    """Vérifie (post-reconnexion) que le node porte bien la config cible."""
    mqtt = node.moduleConfig.mqtt
    report = mqtt.map_report_settings
    pos = node.localConfig.position
    checks = [
        bool(mqtt.enabled),
        bool(mqtt.proxy_to_client_enabled),
        bool(mqtt.encryption_enabled),
        bool(mqtt.json_enabled),
        not mqtt.tls_enabled,
        bool(mqtt.map_reporting_enabled),
        mqtt.address == target_address(args.broker, args.port),
        mqtt.root == args.root,
        report.publish_interval_secs == args.publish_interval,
        report.position_precision == args.precision,
        bool(report.should_report_location) == args.consent,
        pos.position_broadcast_secs == args.broadcast_secs,
        bool(node.channels[0].settings.uplink_enabled),
    ]
    if args.username is not None:
        checks.append(mqtt.username == args.username)
    if args.password is not None:
        checks.append(mqtt.password == args.password)
    if args.fixed_position:
        checks.append(bool(pos.fixed_position))
    return all(checks)


def _close_quietly(iface: Any, thread_factory: Callable[..., Any], timeout: float = CLOSE_JOIN_TIMEOUT) -> None:
    """Ferme l'interface sans JAMAIS bloquer : `close()` meshtastic gèle sur lien mort
    (leçon passerelle) → thread daemon + join court ; le process sort de toute façon."""
    closer = thread_factory(target=iface.close, daemon=True)
    closer.start()
    closer.join(timeout)


def run(
    args: argparse.Namespace,
    *,
    interface_factory: Callable[[str], Any] = default_interface_factory,
    sleep: Callable[[float], None] = time.sleep,
    thread_factory: Callable[..., Any] = threading.Thread,
) -> Tuple[int, Dict[str, Any]]:
    """Exécute --inspect ou --apply. Renvoie (exit code, JSON du contrat).

    Codes de sortie (contrat §2 amendé) : 0 = succès vérifié ; 2 = commité-mais-non-vérifié
    (la transaction est partie mais le node n'a pas ré-annoncé dans le budget — l'appelant
    doit traiter ça comme un succès provisoire et ré-inspecter plus tard) ; 1 = échec dur.
    """
    expected = target_address(args.broker, args.port)
    iface = connect_with_retry(args.mac, factory=interface_factory, sleep=sleep)
    if args.inspect:
        state = read_state(iface)
        _close_quietly(iface, thread_factory)
        return 0, dict(state, **assess(state, expected), applied=False)
    # Identité capturée AVANT le commit : c'est tout ce qu'on saura encore du node si la
    # reconnexion post-reboot échoue (cas exit 2).
    identity = metrics.node_identity(iface.getMyNodeInfo() or {})
    rebooted = apply_target(iface.localNode, args, thread_factory=thread_factory)
    if rebooted:
        # L'interface pré-reboot est morte : on ne la ferme PAS (close() gèlerait),
        # on attend le reboot puis on rouvre une connexion fraîche pour vérifier —
        # avec le budget PATIENT (le node peut mettre ~2 min à ré-annoncer).
        sleep(REBOOT_WAIT)
        try:
            iface = connect_with_retry(
                args.mac, factory=interface_factory, attempts=RECONNECT_ATTEMPTS,
                delay=RECONNECT_DELAY, max_delay=RECONNECT_MAX_DELAY, sleep=sleep,
            )
        except ProvisionError as exc:
            # Commité mais non vérifié ≠ échec : ne PAS renvoyer l'enveloppe {"error"}
            # (l'installateur conclurait à tort à un onboarding raté alors que le write
            # est très probablement appliqué). Contrat : exit 2 + champs dédiés.
            log.warning("commit parti mais vérification impossible : %s", exc)
            return 2, {
                "node_id": identity["node_id"],
                "node_name": identity["node_name"],
                "applied": None,
                "committed": True,
                "verified": False,
                "warning": COMMITTED_UNVERIFIED_WARNING,
            }
    state = read_state(iface)
    applied = matches_target(iface.localNode, args)
    _close_quietly(iface, thread_factory)
    return (0 if applied else 1), dict(state, **assess(state, expected), applied=applied)


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    interface_factory: Callable[[str], Any] = default_interface_factory,
    sleep: Callable[[float], None] = time.sleep,
    thread_factory: Callable[..., Any] = threading.Thread,
    out: Callable[[str], None] = print,
) -> int:
    """Point d'entrée CLI. stdout = UN objet JSON (contrat §2) ; logs sur stderr."""
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [provision] %(message)s")
    try:
        code, payload = run(
            args, interface_factory=interface_factory, sleep=sleep, thread_factory=thread_factory
        )
    except ProvisionError as exc:
        out(json.dumps({"error": str(exc)}))
        return 1
    except Exception as exc:  # noqa: BLE001 — l'installateur parse stdout : toujours du JSON
        out(json.dumps({"error": f"{type(exc).__name__}: {exc}"}))
        return 1
    out(json.dumps(payload))
    return code


def cli(argv: Optional[Sequence[str]] = None, *, terminate: Callable[[int], None] = os._exit, **kwargs) -> None:
    """Frontière OS : imprime le JSON (via `main`) puis SORT DUR (`os._exit`).

    Une `BLEInterface` meshtastic/bleak réelle laisse des threads **non-daemon**
    vivants ; un arrêt normal (`raise SystemExit`) les JOINDRAIT et **gèlerait** le
    process — surtout sur le chemin exit-2 (reconnexion épuisée), où l'interface
    pré-reboot n'est volontairement jamais fermée. Observé au hw-test T114 (2026-07-11) :
    hang ~19 min, JSON jamais émis (stdout bufferisé hors TTY, flush atexit jamais
    atteint). On flush explicitement puis on court-circuite via `os._exit` — la leçon
    fondatrice du projet (cf. `worker.run_worker`). `terminate` est un seam de test.
    """
    code = main(argv, **kwargs)
    sys.stdout.flush()
    sys.stderr.flush()
    terminate(code)


if __name__ == "__main__":  # pragma: no cover
    cli()
