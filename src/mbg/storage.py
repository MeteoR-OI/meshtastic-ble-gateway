# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Mickaël Hoareau — MeteoR-OI
"""Stockage local des métriques (SQLite, stdlib — zéro dépendance).

Deux écrivains (worker : node_metrics + neighbor_registry ; superviseur : link_quality) sur
une même base en mode WAL (concurrence multi-process). Une connexion par opération
(fréquence faible : quelques minutes). Lecture par l'API + export CSV.

Voisins (PORTÉE v2) : un **registre persistant** `neighbor_registry` (une ligne par node,
upsert à chaque sonde, conservation des autres) porte toute la métrique voisinage. Il SURVIT
aux reconnexions (fini le sous-comptage post-restart où `count` restait bloqué). Les vrais
périmés vieillissent par le filtre `last_heard >= now - W` (jamais ré-introduits). L'ancienne
table snapshot `neighbors` (≤ v0.8.2) n'est plus écrite/lue ; elle reste orpheline dans les
bases existantes (aucune perte, ignorée).
"""
from __future__ import annotations

import contextlib
import csv
import os
import sqlite3
import time
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from . import metrics as metrics_mod

# Tables à série temporelle (purge par `ts`, export ordonné par `ts`). Le registre voisins
# n'y est PAS : il s'AGRÈGE (une ligne/node) et ne se purge pas (distinct_total = tout
# l'historique) ; il vieillit uniquement par le filtre d'activité à la lecture.
_TS_TABLES = ("node_metrics", "link_quality")
# (table, colonne d'ordre) pour l'export CSV.
_EXPORT_TABLES: Tuple[Tuple[str, str], ...] = (
    ("node_metrics", "ts"),
    ("link_quality", "ts"),
    ("neighbor_registry", "last_heard"),
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS node_metrics (
  ts REAL, battery_level INTEGER, voltage REAL, channel_util REAL,
  air_util_tx REAL, uptime INTEGER, lat REAL, lon REAL, altitude INTEGER,
  node_id TEXT, node_name TEXT,
  mqtt_broker TEXT, mqtt_proxy_ok INTEGER, mqtt_map_reporting INTEGER,
  max_distance_km REAL
);
CREATE TABLE IF NOT EXISTS neighbor_registry (
  node_id TEXT PRIMARY KEY, last_heard REAL, lat REAL, lon REAL, snr REAL, hops_away INTEGER
);
CREATE TABLE IF NOT EXISTS link_quality (ts REAL, reconnects INTEGER);
CREATE INDEX IF NOT EXISTS idx_node_ts ON node_metrics(ts);
"""

# Colonnes ajoutées après coup (statut MQTT, onboarding) : `CREATE TABLE IF NOT EXISTS`
# n'ajoute jamais de colonne, les bases déjà en prod doivent être migrées à l'init.
_MIGRATIONS = (
    ("node_metrics", "mqtt_broker", "TEXT"),
    ("node_metrics", "mqtt_proxy_ok", "INTEGER"),
    ("node_metrics", "mqtt_map_reporting", "INTEGER"),
    ("node_metrics", "max_distance_km", "REAL"),
)


class MetricsStore:
    def __init__(
        self,
        db_path: str,
        *,
        clock: Callable[[], float] = time.time,
        active_window: float = metrics_mod.NEIGHBOR_ACTIVE_FLOOR,
    ) -> None:
        self._path = db_path
        self._clock = clock
        # Fenêtre "voisin actif" (s) : filtre count/best_snr/max_distance* dans latest().
        # Le superviseur la calcule depuis la config ; défaut = plancher (lecture API robuste).
        self._active_window = active_window
        with self._conn(commit=True) as conn:
            conn.executescript(_SCHEMA)
            self._migrate(conn)
            self._seed_registry_from_legacy(conn)

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        for table, column, sql_type in _MIGRATIONS:
            cols = {row["name"] for row in conn.execute("PRAGMA table_info(%s)" % table)}
            if column not in cols:
                conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, column, sql_type))

    @staticmethod
    def _seed_registry_from_legacy(conn: sqlite3.Connection) -> None:
        """Graine `neighbor_registry` depuis l'ancienne table snapshot `neighbors` (≤ v0.8.2)
        pour préserver la continuité de `distinct_total` au 1er démarrage PORTÉE v2.

        `INSERT OR IGNORE` (le PK node_id existant n'est jamais écrasé) → idempotent et sûr à
        chaque init : ne réintroduit jamais un node déjà connu du registre (donc n'écrase pas
        une position/hops fraîche par les colonnes absentes du snapshot). L'ancien snapshot n'a
        ni lat/lon ni hops_away → seuls node_id + last_heard sont graines (la position/hops se
        remplira au prochain relevé live du node).
        """
        legacy = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='neighbors'"
        ).fetchone()
        if legacy is None:
            return
        conn.execute(
            "INSERT OR IGNORE INTO neighbor_registry (node_id, last_heard) "
            "SELECT node_id, max(last_heard) FROM neighbors "
            "WHERE node_id IS NOT NULL GROUP BY node_id"
        )

    @contextlib.contextmanager
    def _conn(self, commit: bool = False) -> Iterator[sqlite3.Connection]:
        """Connexion à durée de vie bornée (toujours fermée) — évite les fuites."""
        conn = sqlite3.connect(self._path, timeout=5)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            if commit:
                conn.commit()
        finally:
            conn.close()

    def record_node(self, metrics: Dict[str, Any], position: Optional[Dict[str, Any]] = None) -> None:
        pos = position or {}
        with self._conn(commit=True) as conn:
            conn.execute(
                "INSERT INTO node_metrics (ts,battery_level,voltage,channel_util,air_util_tx,"
                "uptime,lat,lon,altitude,node_id,node_name,"
                "mqtt_broker,mqtt_proxy_ok,mqtt_map_reporting,max_distance_km) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    self._clock(), metrics.get("battery_level"), metrics.get("voltage"),
                    metrics.get("channel_util"), metrics.get("air_util_tx"), metrics.get("uptime"),
                    pos.get("lat"), pos.get("lon"), pos.get("altitude"),
                    metrics.get("node_id"), metrics.get("node_name"),
                    metrics.get("mqtt_broker"), metrics.get("mqtt_proxy_ok"),
                    metrics.get("mqtt_map_reporting"), metrics.get("max_distance_km"),
                ),
            )

    def upsert_neighbors(self, neighbors: List[Dict[str, Any]]) -> None:
        """Merge la NodeDB live dans le registre persistant (PORTÉE v2).

        Une ligne par `node_id` : `INSERT OR REPLACE` met à jour les voisins entendus cette
        sonde ; les autres (absents de `neighbors`) sont CONSERVÉS tels quels. `last_heard`
        est l'horloge unix du mesh (monotone par node) → le registre vieillit correctement à
        travers les reconnexions. Aucune ligne n'est supprimée ici.
        """
        with self._conn(commit=True) as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO neighbor_registry "
                "(node_id,last_heard,lat,lon,snr,hops_away) VALUES (?,?,?,?,?,?)",
                [
                    (n.get("node_id"), n.get("last_heard"), n.get("lat"), n.get("lon"),
                     n.get("snr"), n.get("hops_away"))
                    for n in neighbors
                ],
            )

    def record_link(self, reconnects: int) -> None:
        with self._conn(commit=True) as conn:
            conn.execute("INSERT INTO link_quality (ts,reconnects) VALUES (?,?)", (self._clock(), reconnects))

    def latest(self) -> Dict[str, Any]:
        now = self._clock()
        with self._conn() as conn:
            node = conn.execute("SELECT * FROM node_metrics ORDER BY ts DESC LIMIT 1").fetchone()
            link = conn.execute("SELECT * FROM link_quality ORDER BY ts DESC LIMIT 1").fetchone()
            # Voisins ACTIFS (last_heard récent) depuis le registre persistant (PORTÉE v2).
            active = conn.execute(
                "SELECT node_id,last_heard,lat,lon,snr,hops_away FROM neighbor_registry "
                "WHERE last_heard >= ?",
                (now - self._active_window,),
            ).fetchall()
            # DISTINCTS par fenêtre : le registre a une ligne/node, donc un simple comptage.
            distinct = conn.execute(
                "SELECT "
                "sum(CASE WHEN last_heard >= ? THEN 1 ELSE 0 END) AS d1h,"
                "sum(CASE WHEN last_heard >= ? THEN 1 ELSE 0 END) AS d24h,"
                "count(*) AS dtot FROM neighbor_registry",
                (now - 3600, now - 86400),
            ).fetchone()
        neighbors = None
        if distinct["dtot"]:  # au moins un node vu un jour
            gateway = {"lat": node["lat"], "lon": node["lon"]} if node else {}
            direct = [dict(r) for r in active if r["hops_away"] == 0]
            relayed = [dict(r) for r in active if r["hops_away"] is not None and r["hops_away"] >= 1]
            best_snr = max((r["snr"] for r in active if r["snr"] is not None), default=None)
            neighbors = {
                "count": len(active),  # voisins ACTIFS (toutes portées)
                "best_snr": best_snr,
                # DIRECT (0-hop) et MULTI-HOP (relayés) séparés ; 0.0 km valide (co-localisés).
                "max_distance_km": metrics_mod.max_distance_km(gateway, direct),
                "max_distance_hops_km": metrics_mod.max_distance_km(gateway, relayed),
                "distinct_1h": distinct["d1h"],
                "distinct_24h": distinct["d24h"],
                "distinct_total": distinct["dtot"],
            }
        return {
            "node": dict(node) if node else None,
            "link": dict(link) if link else None,
            "neighbors": neighbors,
        }

    def history(self, since: float = 0.0, limit: int = 1000) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM node_metrics WHERE ts>=? ORDER BY ts DESC LIMIT ?", (since, limit)
            ).fetchall()
        return [dict(r) for r in rows]

    def prune(self, older_than_seconds: float) -> None:
        """Purge les séries temporelles anciennes. Le registre voisins n'est PAS purgé
        (il s'agrège ; `distinct_total` = tout l'historique, borné par le nb de nodes)."""
        cutoff = self._clock() - older_than_seconds
        with self._conn(commit=True) as conn:
            for table in _TS_TABLES:
                conn.execute("DELETE FROM %s WHERE ts < ?" % table, (cutoff,))

    def export_csv(self, directory: str) -> None:
        os.makedirs(directory, exist_ok=True)
        with self._conn() as conn:
            for table, order_col in _EXPORT_TABLES:
                rows = conn.execute("SELECT * FROM %s ORDER BY %s" % (table, order_col)).fetchall()
                with open(os.path.join(directory, table + ".csv"), "w", newline="") as f:
                    writer = csv.writer(f)
                    if rows:
                        writer.writerow(rows[0].keys())
                        writer.writerows([list(r) for r in rows])
