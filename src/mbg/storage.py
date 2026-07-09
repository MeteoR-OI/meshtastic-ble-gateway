# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Mickaël Hoareau — MeteoR-OI
"""Stockage local des métriques (SQLite, stdlib — zéro dépendance).

Deux écrivains (worker : node_metrics + neighbors ; superviseur : link_quality) sur
une même base en mode WAL (concurrence multi-process). Une connexion par opération
(fréquence faible : quelques minutes). Lecture par l'API + export CSV.
"""
from __future__ import annotations

import contextlib
import csv
import os
import sqlite3
import time
from typing import Any, Callable, Dict, Iterator, List, Optional

_TABLES = ("node_metrics", "neighbors", "link_quality")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS node_metrics (
  ts REAL, battery_level INTEGER, voltage REAL, channel_util REAL,
  air_util_tx REAL, uptime INTEGER, lat REAL, lon REAL, altitude INTEGER
);
CREATE TABLE IF NOT EXISTS neighbors (
  ts REAL, node_id TEXT, snr REAL, rssi INTEGER, last_heard INTEGER
);
CREATE TABLE IF NOT EXISTS link_quality (ts REAL, reconnects INTEGER);
CREATE INDEX IF NOT EXISTS idx_node_ts ON node_metrics(ts);
"""


class MetricsStore:
    def __init__(self, db_path: str, *, clock: Callable[[], float] = time.time) -> None:
        self._path = db_path
        self._clock = clock
        with self._conn(commit=True) as conn:
            conn.executescript(_SCHEMA)

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
                "uptime,lat,lon,altitude) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    self._clock(), metrics.get("battery_level"), metrics.get("voltage"),
                    metrics.get("channel_util"), metrics.get("air_util_tx"), metrics.get("uptime"),
                    pos.get("lat"), pos.get("lon"), pos.get("altitude"),
                ),
            )

    def record_neighbors(self, neighbors: List[Dict[str, Any]]) -> None:
        ts = self._clock()
        with self._conn(commit=True) as conn:
            conn.executemany(
                "INSERT INTO neighbors (ts,node_id,snr,rssi,last_heard) VALUES (?,?,?,?,?)",
                [(ts, n.get("node_id"), n.get("snr"), n.get("rssi"), n.get("last_heard")) for n in neighbors],
            )

    def record_link(self, reconnects: int) -> None:
        with self._conn(commit=True) as conn:
            conn.execute("INSERT INTO link_quality (ts,reconnects) VALUES (?,?)", (self._clock(), reconnects))

    def latest(self) -> Dict[str, Any]:
        with self._conn() as conn:
            node = conn.execute("SELECT * FROM node_metrics ORDER BY ts DESC LIMIT 1").fetchone()
            link = conn.execute("SELECT * FROM link_quality ORDER BY ts DESC LIMIT 1").fetchone()
        return {"node": dict(node) if node else None, "link": dict(link) if link else None}

    def history(self, since: float = 0.0, limit: int = 1000) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM node_metrics WHERE ts>=? ORDER BY ts DESC LIMIT ?", (since, limit)
            ).fetchall()
        return [dict(r) for r in rows]

    def prune(self, older_than_seconds: float) -> None:
        cutoff = self._clock() - older_than_seconds
        with self._conn(commit=True) as conn:
            for table in _TABLES:
                conn.execute("DELETE FROM %s WHERE ts < ?" % table, (cutoff,))

    def export_csv(self, directory: str) -> None:
        os.makedirs(directory, exist_ok=True)
        with self._conn() as conn:
            for table in _TABLES:
                rows = conn.execute("SELECT * FROM %s ORDER BY ts" % table).fetchall()
                with open(os.path.join(directory, table + ".csv"), "w", newline="") as f:
                    writer = csv.writer(f)
                    if rows:
                        writer.writerow(rows[0].keys())
                        writer.writerows([list(r) for r in rows])
