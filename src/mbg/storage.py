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
import json
import os
import sqlite3
import time
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from . import metrics as metrics_mod

# Tables à série temporelle (purge par `ts`, export ordonné par `ts`). Le registre voisins
# n'y est PAS : il s'AGRÈGE (une ligne/node) et ne se purge pas (distinct_total = tout
# l'historique) ; il vieillit uniquement par le filtre d'activité à la lecture. `node_names`
# non plus, pour la même raison (une ligne/node, et un nom doit survivre aux comptages).
_TS_TABLES = ("node_metrics", "link_quality", "packet_counts")
# (table, colonne d'ordre) pour l'export CSV.
_EXPORT_TABLES: Tuple[Tuple[str, str], ...] = (
    ("node_metrics", "ts"),
    ("link_quality", "ts"),
    ("neighbor_registry", "last_heard"),
    ("traceroute", "sent_epoch"),
    ("packet_counts", "ts"),
    ("node_names", "updated"),
)

# Plafond DUR de rétention de `packet_counts`, INDÉPENDANT de `retention_days` (qui vaut 0 par
# défaut = « pas de purge », cf. config.py). Une série temporelle qui ne se purge jamais est une
# fuite lente : ~5 800 lignes/jour (288 flushes × ~20 nœuds) ⇒ ~200 k lignes à l'équilibre.
# 35 j = fenêtre du chart « mois » + marge. Appliqué par `prune_packets` (voir sa docstring pour
# la raison de la séparation d'avec `prune`), inconditionnellement, à chaque cycle de maintenance.
PACKET_RETENTION_SECONDS = 35 * 86400

# Bornes du re-binning de `/packets` (contrat A) : `bin` ∈ [60 s, 24 h], défaut 300 s.
PACKET_BIN_DEFAULT = 300
PACKET_BIN_MIN = 60
PACKET_BIN_MAX = 86400

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
CREATE TABLE IF NOT EXISTS packet_counts (ts REAL, node_id TEXT, count INTEGER);
CREATE TABLE IF NOT EXISTS node_names (
  node_id TEXT PRIMARY KEY, short_name TEXT, long_name TEXT, updated REAL
);
CREATE TABLE IF NOT EXISTS traceroute (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  request_id INTEGER, dest TEXT NOT NULL, hop_limit INTEGER,
  status TEXT NOT NULL,
  sent_ts TEXT NOT NULL, recv_ts TEXT,
  sent_epoch REAL, recv_epoch REAL,
  rtt_ms INTEGER, hops_to INTEGER, hops_back INTEGER,
  route_json TEXT, source TEXT
);
CREATE INDEX IF NOT EXISTS idx_node_ts ON node_metrics(ts);
CREATE INDEX IF NOT EXISTS idx_traceroute_dest_ts ON traceroute(dest, sent_epoch);
CREATE INDEX IF NOT EXISTS idx_packets_ts ON packet_counts(ts);
"""

# Colonnes ajoutées à node_metrics APRÈS la 1re release : `CREATE TABLE IF NOT EXISTS` n'ajoute
# jamais de colonne, les bases déjà en prod (créées en 0.3/0.4/0.6) doivent être migrées à l'init.
# ⚠️ TOUTE colonne ajoutée à `_SCHEMA` après coup DOIT figurer ici, sinon crash `no column named …`
# sur base pré-existante (bug v0.9.0 : `node_id`/`node_name`, présents dans _SCHEMA depuis v0.7 mais
# jamais migrés → crash-loop du worker sur les bases 0.6.x — CHAR645/MHA235). L'ordre est indifférent
# (chaque ADD COLUMN est indépendant, gardé par le PRAGMA table_info).
_MIGRATIONS = (
    ("node_metrics", "node_id", "TEXT"),  # ajoutée en v0.7 (identité node) — migration manquante <v0.9.1
    ("node_metrics", "node_name", "TEXT"),
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

    # --- Paquets reçus par nœud (histogramme /packets) ---
    def record_packets(self, counts: Dict[str, int]) -> None:
        """Écrit un lot de comptages (une ligne par nœud, `ts` = instant du flush).

        `INSERT`, jamais upsert : c'est une SÉRIE TEMPORELLE (contrairement à
        `neighbor_registry`, qui n'a pas d'historique). Le re-binning se fait à la lecture,
        donc la cadence de flush n'est pas figée dans les données. Lot vide = aucune écriture.
        """
        if not counts:
            return
        ts = self._clock()
        with self._conn(commit=True) as conn:
            conn.executemany(
                "INSERT INTO packet_counts (ts,node_id,count) VALUES (?,?,?)",
                [(ts, node_id, count) for node_id, count in counts.items()],
            )

    def upsert_node_names(self, names: List[Dict[str, Any]]) -> None:
        """Merge les noms de la NodeDB (une ligne par node ; cf. `metrics.node_names`).

        Table d'identité DÉDIÉE, distincte de `neighbor_registry` : celui-ci ne contient que les
        voisins actifs à `hopsAway` connu, donc PAS un sur-ensemble des nœuds comptés — un JOIN
        dessus perdrait le nom de nœuds pourtant présents dans `rows`. Jamais purgée : un nom doit
        survivre aussi longtemps que les comptages qu'il nomme (35 j).
        """
        if not names:
            return
        now = self._clock()
        with self._conn(commit=True) as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO node_names (node_id,short_name,long_name,updated) "
                "VALUES (?,?,?,?)",
                [(n.get("node_id"), n.get("short_name"), n.get("long_name"), now) for n in names],
            )

    def packet_history(
        self, since: float = 0.0, bin_seconds: int = PACKET_BIN_DEFAULT
    ) -> Dict[str, Any]:
        """Histogramme « paquets par nœud, par tranche » — contrat A de `GET /packets`.

        Le re-binning et l'agrégation sont faits EN SQL (jamais en Python) : le consommateur ne
        reçoit jamais de lignes brutes — ~4 800 lignes agrégées au lieu de ~170 k sur la fenêtre
        mois. `CAST(ts/bin AS INT)*bin` = `floor` (les `ts` sont des epochs, donc ≥ 0), adossé à
        `idx_packets_ts` pour le filtre `ts >= since`.

        Une tranche sans paquet pour un nœud n'a PAS de ligne (le remplissage à 0 est la charge
        du consommateur). `nodes` ne contient que les nœuds présents dans `rows`, résolus
        `short_name || long_name || node_id` — jamais absent, jamais null.
        """
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT CAST(ts/? AS INT)*? AS b, node_id, SUM(count) AS c "
                "FROM packet_counts WHERE ts>=? GROUP BY b, node_id ORDER BY b",
                (bin_seconds, bin_seconds, since),
            ).fetchall()
            node_ids = sorted({r["node_id"] for r in rows})
            names: Dict[str, Any] = {}
            if node_ids:
                found = conn.execute(
                    "SELECT node_id, short_name, long_name FROM node_names WHERE node_id IN (%s)"
                    % ",".join("?" * len(node_ids)),
                    node_ids,
                ).fetchall()
                names = {r["node_id"]: (r["short_name"] or r["long_name"]) for r in found}
        return {
            "bin": bin_seconds,
            # Repli sur le node_id : un nœud compté mais jamais nommé (NodeDB sans user, ou
            # entendu entre deux sondes) doit apparaître quand même.
            "nodes": {node_id: (names.get(node_id) or node_id) for node_id in node_ids},
            "rows": [[r["b"], r["node_id"], r["c"]] for r in rows],
        }

    def prune_packets(self, older_than_seconds: float) -> None:
        """Purge `packet_counts` SEULE, au plafond dur (cf. `PACKET_RETENTION_SECONDS`).

        Séparée de `prune()` À DESSEIN : le superviseur l'appelle INCONDITIONNELLEMENT, alors que
        `prune()` reste gouvernée par `retention_days` (0 par défaut = « je garde tout »). Purger
        les autres tables au plafond de 35 j serait une perte de données silencieuse pour une
        station qui a explicitement choisi 0. `node_names` n'est pas purgée (elle s'agrège, et
        doit pouvoir nommer tout comptage encore en fenêtre).

        `packet_counts` est AUSSI dans `_TS_TABLES` : une station qui fixe `retention_days` en
        deçà de 35 j la purge donc plus tôt, via `prune()`. Les deux purges coexistent — 35 j
        est un plafond, pas un plancher.
        """
        cutoff = self._clock() - older_than_seconds
        with self._conn(commit=True) as conn:
            conn.execute("DELETE FROM packet_counts WHERE ts < ?", (cutoff,))

    # --- Traceroute (endpoint + planificateur) ---
    def record_traceroute(self, result: Dict[str, Any], sent_epoch: float, recv_epoch: Optional[float]) -> None:
        """Écrit une ligne traceroute. `result` = dict A.5 (ISO pour l'affichage) ; `*_epoch` =
        temps unix (comparaisons temporelles du planificateur). `route_json` sérialise les 2 legs."""
        route_json = json.dumps(
            {"route_to": result.get("route_to"), "route_back": result.get("route_back")}
        )
        with self._conn(commit=True) as conn:
            conn.execute(
                "INSERT INTO traceroute (request_id,dest,hop_limit,status,sent_ts,recv_ts,"
                "sent_epoch,recv_epoch,rtt_ms,hops_to,hops_back,route_json,source) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    result.get("request_id"), result.get("dest"), result.get("hop_limit"),
                    result.get("status"), result.get("sent_ts"), result.get("recv_ts"),
                    sent_epoch, recv_epoch, result.get("rtt_ms"),
                    result.get("hops_to"), result.get("hops_back"), route_json,
                    result.get("source"),
                ),
            )

    @staticmethod
    def _traceroute_row(row: sqlite3.Row) -> Dict[str, Any]:
        """Reconstruit le dict A.5 (route_to/route_back désérialisés) depuis une ligne SQLite."""
        route = json.loads(row["route_json"]) if row["route_json"] else {}
        return {
            "type": "traceroute",
            "dest": row["dest"],
            "request_id": row["request_id"],
            "status": row["status"],
            "sent_ts": row["sent_ts"],
            "recv_ts": row["recv_ts"],
            "rtt_ms": row["rtt_ms"],
            "hop_limit": row["hop_limit"],
            "hops_to": row["hops_to"],
            "hops_back": row["hops_back"],
            "route_to": route.get("route_to"),
            "route_back": route.get("route_back"),
            "source": row["source"],
        }

    def traceroute_history(self, since: float = 0.0, limit: int = 100) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM traceroute WHERE sent_epoch>=? ORDER BY sent_epoch DESC LIMIT ?",
                (since, limit),
            ).fetchall()
        return [self._traceroute_row(r) for r in rows]

    def traceroute_by_request_id(self, request_id: int) -> Optional[Dict[str, Any]]:
        """Dernière ligne (terminale) pour ce `request_id` — sert au mode `wait:true` de l'API."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM traceroute WHERE request_id=? ORDER BY sent_epoch DESC LIMIT 1",
                (request_id,),
            ).fetchone()
        return self._traceroute_row(row) if row else None

    def traceroute_last_sent(self) -> Optional[float]:
        """Epoch du dernier traceroute émis (toutes sources) — garde-fou min-gap global."""
        with self._conn() as conn:
            row = conn.execute("SELECT max(sent_epoch) AS m FROM traceroute").fetchone()
        return row["m"]

    def traceroute_last_attempt_by_node(self, dest: str) -> Optional[float]:
        """Epoch de la dernière tentative vers ce node (tout statut) — garde-fou min par nœud."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT max(sent_epoch) AS m FROM traceroute WHERE dest=?", (dest,)
            ).fetchone()
        return row["m"]

    def traceroute_last_success_by_node(self) -> Dict[str, float]:
        """`dest -> epoch du dernier traceroute réussi` (statut ok) — priorité de fraîcheur (staleness)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT dest, max(sent_epoch) AS m FROM traceroute WHERE status='ok' GROUP BY dest"
            ).fetchall()
        return {r["dest"]: r["m"] for r in rows}

    def traceroute_count_since(self, since_epoch: float, source_prefix: Optional[str] = None) -> int:
        """Nb de traceroute émis depuis `since_epoch` (optionnellement filtrés `source LIKE prefix%`)
        — sert au budget quotidien du planificateur."""
        sql = "SELECT count(*) AS c FROM traceroute WHERE sent_epoch>=?"
        params: List[Any] = [since_epoch]
        if source_prefix is not None:
            sql += " AND source LIKE ?"
            params.append(source_prefix + "%")
        with self._conn() as conn:
            row = conn.execute(sql, params).fetchone()
        return row["c"]

    def traceroute_counters(self) -> Dict[str, Any]:
        """Compteurs pour `/metrics` (cumulés depuis la base, survivent aux restarts)."""
        with self._conn() as conn:
            agg = conn.execute(
                "SELECT count(*) AS sent,"
                "sum(CASE WHEN status='ok' THEN 1 ELSE 0 END) AS ok,"
                "sum(CASE WHEN status='timeout' THEN 1 ELSE 0 END) AS timeout,"
                "sum(CASE WHEN status='error' THEN 1 ELSE 0 END) AS error "
                "FROM traceroute"
            ).fetchone()
            last = conn.execute(
                "SELECT rtt_ms FROM traceroute WHERE status='ok' ORDER BY sent_epoch DESC LIMIT 1"
            ).fetchone()
        return {
            "traceroute_sent_total": agg["sent"] or 0,
            "traceroute_ok_total": agg["ok"] or 0,
            "traceroute_timeout_total": agg["timeout"] or 0,
            "traceroute_error_total": agg["error"] or 0,
            "traceroute_last_rtt_ms": last["rtt_ms"] if last else None,
        }

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
            # traceroute : série temporelle aussi, mais ordonnée par sent_epoch (pas `ts`).
            conn.execute("DELETE FROM traceroute WHERE sent_epoch < ?", (cutoff,))

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
