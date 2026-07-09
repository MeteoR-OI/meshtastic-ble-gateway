# SPDX-License-Identifier: AGPL-3.0-or-later
from mbg.tiers import CRITICAL, HIGH, LOW, MID, select_tier


def test_tier_by_level_no_history():
    # sans palier courant : sélection directe par niveau (défaut HIGH si None)
    assert select_tier(90, None, 3) is HIGH
    assert select_tier(60, None, 3) is MID
    assert select_tier(30, None, 3) is LOW
    assert select_tier(10, None, 3) is CRITICAL


def test_level_none_keeps_current():
    assert select_tier(None, MID, 3) is MID
    assert select_tier(None, None, 3) is HIGH  # défaut au démarrage


def test_descend_at_nominal_threshold():
    # depuis HIGH, on descend à MID dès que la batterie passe sous 75 (marge 0 à la descente)
    assert select_tier(74, HIGH, 3) is MID
    assert select_tier(24, LOW, 3) is CRITICAL  # entrée duty-cycle sous 25


def test_ascend_requires_threshold_plus_hysteresis():
    # depuis MID, il faut dépasser 75 + 3 = 78 pour remonter en HIGH
    assert select_tier(76, MID, 3) is MID  # dans la bande [75, 78] -> reste MID
    assert select_tier(77, MID, 3) is MID
    assert select_tier(79, MID, 3) is HIGH  # au-delà de 78 -> remonte


def test_no_flapping_on_measurement_noise():
    # bruit 74/76 autour de 75 : une fois descendu en MID, 76 ne fait pas remonter
    tier = HIGH
    tier = select_tier(76, tier, 3)  # encore HIGH (>75)
    assert tier is HIGH
    tier = select_tier(74, tier, 3)  # descend en MID
    assert tier is MID
    tier = select_tier(76, tier, 3)  # bruit remonte à 76 -> reste MID (pas de flapping)
    assert tier is MID


def test_critical_hysteresis_sticky():
    # sortie du duty-cycle seulement au-delà de 25 + 3 = 28
    assert select_tier(26, CRITICAL, 3) is CRITICAL  # 26 < 28 -> reste critique
    assert select_tier(29, CRITICAL, 3) is LOW       # au-delà de 28 -> sort


def test_multi_tier_drop_in_one_step():
    # chute brutale : on peut sauter plusieurs paliers d'un coup (marge 0 à la descente)
    assert select_tier(10, HIGH, 3) is CRITICAL
