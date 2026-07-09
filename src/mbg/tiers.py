# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Mickaël Hoareau — MeteoR-OI
"""Paliers batterie (V0.4) : cadence de monitoring + duty-cycle selon la batterie du node.

Fonction pure `select_tier` (testable sans matériel) avec **hystérésis collante vers le
haut** : on descend d'un palier au franchissement nominal du seuil, mais on ne remonte
qu'après seuil + hystérésis. Ça évite le flapping sur le bruit de mesure et, surtout, sur
le seuil critique 25 % (entrée/sortie du duty-cycle). Aucun magic number : tous les seuils
et cadences sont des constantes nommées ici.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

# Seuils de batterie (%) — bornes basses de chaque palier « live ».
TIER_HIGH_MIN = 75.0
TIER_MID_MIN = 50.0
TIER_LOW_MIN = 25.0

# Cadence de relevé des métriques (s) par palier.
TIER_HIGH_INTERVAL = 900.0   # 15 min
TIER_MID_INTERVAL = 1800.0   # 30 min
TIER_LOW_INTERVAL = 3600.0   # 60 min


@dataclass(frozen=True)
class Tier:
    """Un palier : nom, cadence de monitoring, et si le lien BLE est duty-cyclé.

    `monitor_interval is None` (palier CRITICAL) signale au superviseur d'utiliser
    `duty_on` comme cadence (1 relevé par fenêtre de connexion).
    """

    name: str
    monitor_interval: Optional[float]
    duty_cycle: bool


HIGH = Tier("HIGH", TIER_HIGH_INTERVAL, False)
MID = Tier("MID", TIER_MID_INTERVAL, False)
LOW = Tier("LOW", TIER_LOW_INTERVAL, False)
CRITICAL = Tier("CRITICAL", None, True)

# Ordre du plus haut (batterie pleine) au plus bas (critique).
TIER_ORDER: List[Tier] = [HIGH, MID, LOW, CRITICAL]

# Seuils de descente, alignés sur TIER_ORDER : franchir vers le bas TIER_ORDER[i]->[i+1].
_THRESHOLDS: Tuple[float, ...] = (TIER_HIGH_MIN, TIER_MID_MIN, TIER_LOW_MIN)


def _count_below(level: float, offset: float) -> int:
    """Nombre de seuils que `level` passe sous (avec une marge `offset`) = index de palier."""
    return sum(1 for t in _THRESHOLDS if level < t + offset)


def select_tier(level: Optional[float], current: Optional[Tier], hysteresis: float) -> Tier:
    """Palier pour `level` (%), sticky autour de `current` via `hysteresis` (%).

    Descente : au franchissement nominal du seuil (marge 0). Remontée : seulement après
    seuil + hystérésis. `level is None` (batterie inconnue) → on garde le palier courant
    (défaut HIGH au démarrage).
    """
    ci = TIER_ORDER.index(current) if current in TIER_ORDER else 0
    if level is None:
        return TIER_ORDER[ci]
    down = _count_below(level, 0.0)          # jusqu'où on descendrait sans marge
    up = _count_below(level, hysteresis)     # remontée : il faut dépasser seuil + hystérésis
    if down > ci:
        return TIER_ORDER[down]              # la batterie a franchi un seuil vers le bas
    if up < ci:
        return TIER_ORDER[up]                # la batterie est repassée au-dessus (seuil + hyst)
    return TIER_ORDER[ci]                     # dans la bande d'hystérésis → on reste
