# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Mickaël Hoareau — MeteoR-OI
"""Planificateur de traceroute automatiques (opt-in, parcimonie airtime).

Tourne DANS le worker (réutilise l'interface BLE vivante via `TracerouteCoordinator`, jamais
de 2ᵉ connexion). À chaque « tick », `decide(now)` applique les garde-fous puis la **politique
de sélection** (enfichable) et rend au plus **une** cible — sinon `None` (skip). Le traceroute
Meshtastic est un flood coûteux, rate-limité par le firmware : on vise quelques traceroute/jour,
défauts très conservateurs (budget, min-gap, min/nœud, heures calmes, garde chan-util).

Politiques : `static` (round-robin sur une liste fixe) et `staleness` (défaut : le node entendu
récemment dont le dernier traceroute réussi est le plus ancien — jamais-tracé = priorité max).
Points d'extension `recent`/`adaptive` : ajouter une entrée à `_POLICIES`.

Tout est injecté (horloge, heure locale, RNG de jitter, accès store/NodeDB) → 100 % testable.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

log = logging.getLogger("mbg.traceroute.scheduler")

BIG_STALENESS = 1e12  # score d'un node jamais tracé (priorité maximale en staleness)
PRIORITY_WEIGHT = 2.0  # multiplicateur de score des nodes listés dans `priority`


def parse_quiet_hours(spec: str) -> Optional[Tuple[int, int]]:
    """`"22:00-06:00"` → `(1320, 360)` (minutes depuis minuit). `""`/invalide → None (aucune plage)."""
    if not spec or not spec.strip():
        return None
    try:
        start_s, end_s = spec.split("-")
        start = _hhmm_to_minutes(start_s)
        end = _hhmm_to_minutes(end_s)
    except (ValueError, AttributeError):
        log.warning("MBG_TRACEROUTE_QUIET_HOURS invalide (ignoré): %r", spec)
        return None
    return start, end


def _hhmm_to_minutes(hhmm: str) -> int:
    hh, mm = hhmm.strip().split(":")
    h, m = int(hh), int(mm)
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError("heure hors bornes")
    return h * 60 + m


def in_quiet_hours(minute_of_day: int, window: Optional[Tuple[int, int]]) -> bool:
    """Vrai si `minute_of_day` tombe dans la plage calme (gère le passage de minuit)."""
    if window is None:
        return False
    start, end = window
    if start <= end:
        return start <= minute_of_day < end
    return minute_of_day >= start or minute_of_day < end  # plage à cheval sur minuit


def select_static(
    targets: List[str], start_index: int, eligible: Callable[[str], bool]
) -> Tuple[Optional[str], int]:
    """Round-robin : depuis `start_index`, renvoie la 1re cible `eligible` et l'index suivant.

    Déterministe. `None` (et un index inchangé, avancé d'un tour) si aucune cible n'est éligible
    (toutes en cooldown per-node)."""
    n = len(targets)
    if n == 0:
        return None, start_index
    for offset in range(n):
        idx = (start_index + offset) % n
        if eligible(targets[idx]):
            return targets[idx], (idx + 1) % n
    return None, start_index % n


def select_staleness(
    candidates: List[str],
    last_success: Dict[str, Optional[float]],
    priority: List[str],
    now: float,
) -> Optional[str]:
    """Choisit le candidat au score de fraîcheur le plus élevé (dernier succès le plus ancien).

    Score = ancienneté du dernier traceroute réussi (jamais tracé → `BIG_STALENESS`), pondéré
    ×`PRIORITY_WEIGHT` si le node est listé prioritaire. `None` si aucun candidat."""
    best: Optional[str] = None
    best_score = -1.0
    for node in candidates:
        ts = last_success.get(node)
        base = BIG_STALENESS if ts is None else max(0.0, now - ts)
        score = base * (PRIORITY_WEIGHT if node in priority else 1.0)
        if score > best_score:
            best_score = score
            best = node
    return best


class TracerouteScheduler:
    """Décide, tick par tick, s'il faut lancer un traceroute et vers quelle cible.

    Frontières injectées : `store` (historique/état SQLite), `nodes_fn()`→NodeDB `nodesByNum`,
    `my_num_fn()`→num local, `chanutil_fn()`→`channel_utilization` local (%), `clock`,
    `localtime` (heure station pour heures calmes/minuit), `jitter_fn()`∈[0,1) (anti heure ronde).
    """

    def __init__(
        self,
        config,
        store,
        *,
        nodes_fn: Callable[[], Dict[int, Any]],
        my_num_fn: Callable[[], Optional[int]],
        chanutil_fn: Callable[[], Optional[float]],
        clock: Callable[[], float] = time.time,
        localtime: Callable[[float], time.struct_time] = time.localtime,
        jitter_fn: Callable[[], float] = None,
    ) -> None:
        self._c = config
        self._store = store
        self._nodes_fn = nodes_fn
        self._my_num_fn = my_num_fn
        self._chanutil_fn = chanutil_fn
        self._clock = clock
        self._localtime = localtime
        if jitter_fn is None:  # défaut : RNG standard (déterministe en test via injection)
            import random

            rng = random.Random()
            jitter_fn = rng.random
        self._jitter_fn = jitter_fn
        self._quiet = parse_quiet_hours(config.traceroute_quiet_hours)
        self._static_index = 0
        self._jitter = jitter_fn()  # tirage courant (rafraîchi après chaque émission)
        self._last_tick = 0.0

    # --- Cadence : le worker appelle due()/decide() à chaque poll ---
    def due(self, now: float) -> bool:
        """Vrai si un tick d'évaluation est dû (période `traceroute_tick_s`)."""
        if now - self._last_tick < self._c.traceroute_tick_s:
            return False
        self._last_tick = now
        return True

    def poll(self) -> Optional[Dict[str, Any]]:
        """Appelé à chaque poll du worker : lit l'horloge, vérifie la cadence, décide. None si rien."""
        now = self._clock()
        if not self.due(now):
            return None
        return self.decide(now)

    def _effective_min_gap(self) -> float:
        """Min-gap global + jitter (∈[0, min_gap]) → l'émission ne tombe pas sur l'heure ronde."""
        return self._c.traceroute_min_gap_s * (1.0 + self._jitter)

    def _per_node_ok(self, dest: str, now: float) -> bool:
        last = self._store.traceroute_last_attempt_by_node(dest)
        return last is None or (now - last) >= self._c.traceroute_per_node_min_s

    def _budget_left(self, now: float) -> bool:
        midnight = self._local_midnight(now)
        used = self._store.traceroute_count_since(midnight, source_prefix="scheduler:")
        return used < self._c.traceroute_daily_budget

    def _local_midnight(self, now: float) -> float:
        tm = self._localtime(now)
        return now - (tm.tm_hour * 3600 + tm.tm_min * 60 + tm.tm_sec)

    def decide(self, now: float) -> Optional[Dict[str, Any]]:
        """Applique garde-fous puis politique. Renvoie la commande traceroute, ou None (skip).

        Les raisons de skip sont loguées en DEBUG (observabilité sans bruit INFO)."""
        # 1. Heures calmes (TZ station).
        tm = self._localtime(now)
        if in_quiet_hours(tm.tm_hour * 60 + tm.tm_min, self._quiet):
            log.debug("[traceroute:sched] skip — heures calmes")
            return None
        # 2. Min-gap global (+ jitter).
        last_sent = self._store.traceroute_last_sent()
        if last_sent is not None and (now - last_sent) < self._effective_min_gap():
            log.debug("[traceroute:sched] skip — min-gap")
            return None
        # 3. Budget quotidien.
        if not self._budget_left(now):
            log.debug("[traceroute:sched] skip — budget épuisé")
            return None
        # 4. Garde chan-util local (skip si le mesh local est déjà chargé ; None = inconnu → OK).
        chanutil = self._chanutil_fn()
        if chanutil is not None and chanutil > self._c.traceroute_max_chanutil:
            log.debug("[traceroute:sched] skip — chan-util %.0f%% > %.0f%%", chanutil, self._c.traceroute_max_chanutil)
            return None
        # 5. Sélection selon la politique.
        dest = self._select(now)
        if dest is None:
            log.debug("[traceroute:sched] skip — aucune cible éligible")
            return None
        self._jitter = self._jitter_fn()  # nouveau jitter pour la prochaine fenêtre
        log.info("[traceroute:sched] cible choisie %s (policy=%s)", dest, self._c.traceroute_policy)
        return {
            "type": "traceroute",
            "dest": dest,
            "hop_limit": self._c.traceroute_hop_limit,
            "channel_index": 0,
            "timeout_s": 30.0,
            "source": "scheduler:%s" % self._c.traceroute_policy,
        }

    def _select(self, now: float) -> Optional[str]:
        policy = self._c.traceroute_policy
        selector = _POLICIES.get(policy)
        if selector is None:
            log.warning("[traceroute:sched] policy inconnue %r — skip", policy)
            return None
        return selector(self, now)

    def _select_static(self, now: float) -> Optional[str]:
        dest, self._static_index = select_static(
            list(self._c.traceroute_targets), self._static_index,
            eligible=lambda d: self._per_node_ok(d, now),
        )
        return dest

    def _select_staleness(self, now: float) -> Optional[str]:
        candidates = self._eligible_recent(now)
        if not candidates:
            return None
        last_success = self._store.traceroute_last_success_by_node()
        return select_staleness(candidates, last_success, list(self._c.traceroute_priority), now)

    def _eligible_recent(self, now: float) -> List[str]:
        """Nodes entendus dans les `recent_h` dernières heures ET hors cooldown per-node."""
        window = self._c.traceroute_recent_h * 3600
        my_num = self._my_num_fn()
        out: List[str] = []
        for num, node in (self._nodes_fn() or {}).items():
            if num == my_num:
                continue
            last_heard = node.get("lastHeard")
            if last_heard is None or (now - last_heard) > window:
                continue
            node_id = (node.get("user") or {}).get("id") or ("!%08x" % (num & 0xFFFFFFFF))
            if self._per_node_ok(node_id, now):
                out.append(node_id)
        return out


# Registre des politiques (point d'extension : ajouter recent/adaptive ici sans refactor).
_POLICIES: Dict[str, Callable[["TracerouteScheduler", float], Optional[str]]] = {
    "static": TracerouteScheduler._select_static,
    "staleness": TracerouteScheduler._select_staleness,
}
