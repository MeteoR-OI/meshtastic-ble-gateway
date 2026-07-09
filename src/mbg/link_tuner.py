# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Mickaël Hoareau — MeteoR-OI
"""Stabilisation du lien BLE sur signal faible (V0.5).

Sur un lien faible (-80/-90 dBm), le node « churn » (coupe/relance toutes les 2-3 min).
La coupure est déclenchée par le **supervision timeout** BLE : temps max sans paquet reçu
avant que le lien soit déclaré mort. Sur BlueZ (RPi central) ce défaut est **420 ms** — à
50 ms d'intervalle, ~8 événements manqués (~0,4 s de fading) suffisent à couper.

Le node préférerait 2 s, mais le CENTRAL (RPi) décide, et BlueZ 5.55 **ignore** la debugfs
`supervision_timeout` en rôle central (bug bluez #717 — vérifié terrain via btmon). Le seul
levier qui tienne est une **`LE Connection Update` initiée par le central sur le lien vivant**
(`hcitool lecup`), qui impose le supervision timeout après l'établissement (prouvé terrain :
churn ~19-27/h → ~1,5/h à 6 s de timeout).

Comme chaque connexion BLE = une session worker, on applique le réglage **une fois par
session** (après l'établissement du lien). Nécessite `CAP_NET_ADMIN`+`CAP_NET_RAW` sur le
service (émission d'une commande HCI). Opt-in via `MBG_BLE_SUPERVISION_TIMEOUT_MS` (0 = off).
Ne lève JAMAIS : un échec (droits, pas de connexion, hcitool absent) est logué, la session
continue — au pire on retombe sur le churn d'origine, jamais pire.
"""
from __future__ import annotations

import logging
import subprocess
from typing import Callable, List, Optional

from .config import Config

log = logging.getLogger("mbg.link_tuner")

# Constantes de réglage (aucun magic number). Intervalle ré-asserté à l'identique de ce que
# BlueZ négocie déjà (30/50 ms, honoré par la debugfs) ; seul le supervision timeout change.
CONN_MIN_MS = 30.0  # intervalle de connexion min
CONN_MAX_MS = 50.0  # intervalle de connexion max
LATENCY = 0  # slave latency : 0 (le node force 0 ; n'élargit pas la tolérance)
HCI_CALL_TIMEOUT = 5.0  # garde-fou : un hcitool figé ne doit pas bloquer le heartbeat

# Bornes spec BLE des unités transmises à hcitool.
_INTERVAL_UNIT_MS = 1.25  # unité de l'intervalle de connexion
_INTERVAL_MIN, _INTERVAL_MAX = 6, 3200
_TIMEOUT_UNIT_MS = 10.0  # unité du supervision timeout
_TIMEOUT_MIN, _TIMEOUT_MAX = 10, 3200


def _to_interval_units(ms: float) -> int:
    """Convertit un intervalle en ms vers l'unité BLE (1,25 ms), borné à la plage spec."""
    return max(_INTERVAL_MIN, min(_INTERVAL_MAX, round(ms / _INTERVAL_UNIT_MS)))


def _to_timeout_units(ms: float) -> int:
    """Convertit un supervision timeout en ms vers l'unité BLE (10 ms), borné à la plage spec."""
    return max(_TIMEOUT_MIN, min(_TIMEOUT_MAX, round(ms / _TIMEOUT_UNIT_MS)))


def supervision_ok(timeout_ms: float, max_interval_ms: float = CONN_MAX_MS, latency: int = LATENCY) -> bool:
    """Contrainte spec BLE : supervision_timeout > (1 + latency) × intervalle_max × 2."""
    return timeout_ms > (1 + latency) * max_interval_ms * 2


def parse_handle(con_output: str, address: str) -> Optional[int]:
    """Extrait le handle HCI de la connexion LE du node depuis la sortie de `hcitool con`.

    Renvoie None si le node n'est pas dans la liste (déconnecté) ou si la ligne est illisible.
    """
    target = address.strip().upper()
    for line in con_output.splitlines():
        upper = line.upper()
        if target in upper and "HANDLE" in upper:
            tokens = line.split()
            for i, tok in enumerate(tokens):
                if tok.lower() == "handle" and i + 1 < len(tokens):
                    try:
                        return int(tokens[i + 1])
                    except ValueError:
                        return None
    return None


def build_lecup_argv(handle: int, timeout_ms: float) -> List[str]:
    """Construit la commande `hcitool lecup` (LE Connection Update) pour ce handle."""
    return [
        "hcitool", "lecup",
        "--handle", str(handle),
        "--min", str(_to_interval_units(CONN_MIN_MS)),
        "--max", str(_to_interval_units(CONN_MAX_MS)),
        "--latency", str(LATENCY),
        "--timeout", str(_to_timeout_units(timeout_ms)),
    ]


def tune_link(config: Config, *, run: Callable = subprocess.run) -> bool:
    """Applique le supervision timeout au lien BLE vivant du node. Renvoie True si appliqué.

    Ne lève jamais : tout échec est logué et renvoie False (la session continue).
    """
    timeout_ms = config.ble_supervision_timeout_ms
    if timeout_ms <= 0:
        return False  # désactivé (garde-fou ; normalement pas câblé dans ce cas)
    if not supervision_ok(timeout_ms):
        log.warning(
            "stabilisation BLE ignorée : supervision_timeout %d ms trop court pour l'intervalle",
            timeout_ms,
        )
        return False
    try:
        con = run(["hcitool", "con"], capture_output=True, text=True, timeout=HCI_CALL_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 — jamais de crash de session à cause du tuning
        log.warning("stabilisation BLE : `hcitool con` a échoué (%s)", exc)
        return False
    handle = parse_handle(getattr(con, "stdout", "") or "", config.ble_address)
    if handle is None:
        log.debug("stabilisation BLE : aucune connexion pour %s (rien à régler)", config.ble_address)
        return False
    try:
        result = run(build_lecup_argv(handle, timeout_ms), capture_output=True, text=True, timeout=HCI_CALL_TIMEOUT)
    except Exception as exc:  # noqa: BLE001
        log.warning("stabilisation BLE : `hcitool lecup` a échoué (%s) — CAP_NET_ADMIN présent ?", exc)
        return False
    if result.returncode != 0:
        log.warning(
            "stabilisation BLE : lecup code %s (%s) — CAP_NET_ADMIN présent ?",
            result.returncode, (getattr(result, "stderr", "") or "").strip(),
        )
        return False
    log.info("stabilisation BLE : supervision_timeout=%d ms appliqué (handle %d)", timeout_ms, handle)
    return True
