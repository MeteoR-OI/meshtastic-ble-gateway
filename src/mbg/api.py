# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Mickaël Hoareau — MeteoR-OI
"""API HTTP de contrôle (downlink) — stdlib, zéro dépendance.

`handle_request` est **pur** (auth token + routage + validation) → testable à
100 %. `serve` est l'adaptateur socket/thread (frontière OS, testé en intégration).
Auth par en-tête `X-API-Token`. Routes : POST /send/text, /send/telemetry,
/send/position, /request/position, /admin ; GET /health, /info, /metrics, /history,
/packets (histogramme paquets par nœud), /hops (histogramme paquets par nombre de sauts). Les
commandes POST sont relayées au worker via `dispatch` (déjà borné en
timeout) qui renvoie `{"ok": bool, ...}`.
"""
from __future__ import annotations

import hmac
import json
import logging
import time
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from . import storage
from .traceroute import normalize_dest

log = logging.getLogger("mbg.api")

# Statuts terminaux d'un traceroute (une ligne SQLite n'est écrite qu'à la fin).
_TERMINAL = ("ok", "timeout", "error")


class TracerouteReader:
    """Vue lecture de l'historique traceroute pour l'API (côté superviseur, base WAL).

    Le résultat d'un traceroute arrive de façon asynchrone DANS le worker (boucle de réception) ;
    l'API le récupère en relisant la ligne SQLite. `wait()` fait ce sondage (mode `wait:true`),
    borné par `timeout_s` + marge. `sleep`/`clock` injectables (100 % testable sans horloge réelle).
    """

    def __init__(
        self,
        store,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        poll_interval: float = 0.5,
        margin: float = 5.0,
    ) -> None:
        self._store = store
        self._sleep = sleep
        self._clock = clock
        self._poll_interval = poll_interval
        self._margin = margin

    def history(self, since: float, limit: int):
        return self._store.traceroute_history(since, limit)

    def counters(self):
        return self._store.traceroute_counters()

    def wait(self, request_id: int, timeout_s: float) -> Optional[Dict[str, Any]]:
        """Sonde la base jusqu'à la ligne terminale de ce `request_id`, ou None au bout du délai."""
        deadline = self._clock() + timeout_s + self._margin
        while True:
            row = self._store.traceroute_by_request_id(request_id)
            if row is not None and row.get("status") in _TERMINAL:
                return row
            if self._clock() >= deadline:
                return None
            self._sleep(self._poll_interval)


def _authorized(headers, token: str) -> bool:
    provided = headers.get("X-API-Token") or ""
    return hmac.compare_digest(provided, token)


def _command_for(path: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if path == "/send/text":
        return {
            "type": "text",
            "text": payload.get("text"),
            "channel": payload.get("channel", 0),
            "dest": payload.get("dest"),
            "want_ack": payload.get("want_ack", False),
        }
    if path == "/send/telemetry":
        # `dest` -> requête de télémétrie à un node distant (wantResponse) ; sinon diffusion locale.
        return {"type": "telemetry", "dest": payload.get("dest"), "channel": payload.get("channel", 0)}
    if path == "/send/position":
        # lat/lon/alt optionnels : absents -> ré-émet la position FIXE du node (jamais 0,0).
        return {"type": "position", "lat": payload.get("lat"), "lon": payload.get("lon"), "alt": payload.get("alt")}
    if path == "/request/position":
        # demande à un node distant de renvoyer sa position (wantResponse) ; `dest` requis.
        return {"type": "request_position", "dest": payload.get("dest"), "channel": payload.get("channel", 0)}
    if path == "/admin":
        return {"type": "admin", "setting": payload.get("setting"), "value": payload.get("value")}
    return None


def _validate_traceroute(payload: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Valide/normalise le corps `/traceroute` → (command, None) ou (None, message d'erreur 400)."""
    try:
        _, dest_id = normalize_dest(payload.get("dest"))
    except ValueError as exc:
        return None, str(exc)
    hop_limit = payload.get("hop_limit", 7)
    if not isinstance(hop_limit, int) or isinstance(hop_limit, bool) or not (1 <= hop_limit <= 7):
        return None, "hop_limit hors bornes [1..7]"
    channel_index = payload.get("channel_index", 0)
    if not isinstance(channel_index, int) or isinstance(channel_index, bool) or channel_index < 0:
        return None, "channel_index invalide"
    timeout_s = payload.get("timeout_s", 30)
    if not isinstance(timeout_s, (int, float)) or isinstance(timeout_s, bool) or not (5 <= timeout_s <= 60):
        return None, "timeout_s hors bornes [5..60]"
    command = {
        "type": "traceroute", "dest": dest_id, "hop_limit": hop_limit,
        "channel_index": channel_index, "timeout_s": float(timeout_s), "source": "api",
    }
    return command, None


def _handle_traceroute(payload, dispatch, traceroute) -> Tuple[int, Dict[str, Any]]:
    """POST /traceroute : valide, enfile vers le worker, puis async (202) ou bloquant (`wait`)."""
    command, err = _validate_traceroute(payload)
    if err is not None:
        return 400, {"ok": False, "error": err}
    result = dispatch(command)
    if not result.get("ok"):
        return _status_for(result), result
    request_id = result.get("request_id")
    dest = result.get("dest")
    if not payload.get("wait"):  # mode async (défaut) : le résultat partira en MQTT + SQLite
        return 202, {"status": "accepted", "dest": dest, "request_id": request_id}
    # Mode bloquant : relire la ligne SQLite jusqu'au résultat (ou timeout).
    row = traceroute.wait(request_id, command["timeout_s"]) if traceroute is not None else None
    if row is None:
        return 504, {"status": "timeout", "dest": dest, "request_id": request_id}
    return (504 if row.get("status") == "timeout" else 200), row


def _status_for(result: Dict[str, Any]) -> int:
    if result.get("ok"):
        return 200
    err = result.get("error", "")
    if "aucun worker" in err:
        return 503
    if "timeout" in err:
        return 504
    return 400


def _bool_or_none(value: Any) -> Optional[bool]:
    """SQLite rend les booléens en 0/1 (et NULL en None) — on ré-expose un vrai bool."""
    return None if value is None else bool(value)


def _handle_get(route: str, query: str, metrics, info, traceroute) -> Tuple[int, Dict[str, Any]]:
    if route == "/health":
        return 200, {"ok": True, "status": "up"}
    if route == "/info":
        # Découverte (version + identité node + statut onboarding, CONTRACTS §3) —
        # ex. tuile installer, obs weewx-mbg. Tout vient de la sonde (dernier relevé).
        node = (metrics.latest().get("node") if metrics is not None else None) or {}
        return 200, dict(
            info or {},
            node_id=node.get("node_id"),
            node_name=node.get("node_name"),
            broker=node.get("mqtt_broker"),
            mqtt_proxy_ok=_bool_or_none(node.get("mqtt_proxy_ok")),
            map_reporting=_bool_or_none(node.get("mqtt_map_reporting")),
        )
    params = parse_qs(query)
    # /history?type=traceroute : historique des traceroute (indépendant du monitoring).
    if route == "/history" and params.get("type", [""])[0] == "traceroute":
        if traceroute is None:
            return 404, {"ok": False, "error": "traceroute désactivé"}
        try:
            since = float(params.get("since", ["0"])[0])
            limit = int(params.get("limit", ["100"])[0])
        except ValueError:
            return 400, {"ok": False, "error": "paramètres invalides"}
        return 200, {"rows": traceroute.history(since, limit)}
    # /packets : histogramme « paquets par nœud, par tranche » (contrat A). Non authentifié
    # comme /metrics et /info (le token ne garde que les POST). Agrégation faite en SQL.
    if route == "/packets":
        if metrics is None:  # miroir exact du 404 de /metrics
            return 404, {"ok": False, "error": "monitoring désactivé"}
        try:
            since = float(params.get("since", ["0"])[0])
            bin_seconds = int(params.get("bin", [str(storage.PACKET_BIN_DEFAULT)])[0])
        except ValueError:
            return 400, {"ok": False, "error": "paramètres invalides"}
        if not (storage.PACKET_BIN_MIN <= bin_seconds <= storage.PACKET_BIN_MAX):
            return 400, {"ok": False, "error": "paramètres invalides"}
        return 200, metrics.packet_history(since, bin_seconds)
    # /hops : histogramme « paquets par nombre de sauts, par tranche » (contrat A). Frère strict de
    # /packets — mêmes bornes de bin, même 404 monitoring, mêmes 400. Authentifié comme TOUTES les
    # routes (_authorized tourne avant ce dispatch). Agrégation faite en SQL (≤ 10 buckets).
    if route == "/hops":
        if metrics is None:  # miroir exact du 404 de /metrics et /packets
            return 404, {"ok": False, "error": "monitoring désactivé"}
        try:
            since = float(params.get("since", ["0"])[0])
            bin_seconds = int(params.get("bin", [str(storage.PACKET_BIN_DEFAULT)])[0])
        except ValueError:
            return 400, {"ok": False, "error": "paramètres invalides"}
        if not (storage.PACKET_BIN_MIN <= bin_seconds <= storage.PACKET_BIN_MAX):
            return 400, {"ok": False, "error": "paramètres invalides"}
        return 200, metrics.packet_hops_history(since, bin_seconds)
    if route in ("/metrics", "/history"):
        if metrics is None:
            return 404, {"ok": False, "error": "monitoring désactivé"}
        if route == "/metrics":
            data = metrics.latest()
            if traceroute is not None:  # compteurs traceroute (A.7)
                data = dict(data, traceroute=traceroute.counters())
            return 200, data
        try:
            since = float(params.get("since", ["0"])[0])
            limit = int(params.get("limit", ["1000"])[0])
        except ValueError:
            return 400, {"ok": False, "error": "paramètres invalides"}
        return 200, {"rows": metrics.history(since, limit)}
    return 404, {"ok": False, "error": "route inconnue"}


def handle_request(
    method: str,
    path: str,
    headers,
    body: str,
    token: str,
    dispatch: Callable[[Dict[str, Any]], Dict[str, Any]],
    metrics=None,
    info=None,
    traceroute=None,
) -> Tuple[int, Dict[str, Any]]:
    """Traite une requête (pur). `dispatch` relaie au worker ; `metrics` lit la base (GET) ;
    `info` = infos statiques (version, config) exposées par `/info` ; `traceroute` = lecteur
    d'historique traceroute (GET /history?type=traceroute, /metrics counters, mode wait)."""
    if not _authorized(headers, token):
        return 401, {"ok": False, "error": "non autorisé"}
    parsed = urlparse(path)
    if method == "GET":
        return _handle_get(parsed.path, parsed.query, metrics, info, traceroute)
    if method == "POST":
        try:
            payload = json.loads(body) if body else {}
        except ValueError:
            return 400, {"ok": False, "error": "JSON invalide"}
        if parsed.path == "/traceroute":
            return _handle_traceroute(payload, dispatch, traceroute)
        command = _command_for(parsed.path, payload)
        if command is None:
            return 404, {"ok": False, "error": "route inconnue"}
        result = dispatch(command)
        return _status_for(result), result
    return 404, {"ok": False, "error": "route inconnue"}


def serve(host, port, token, timeout, submit, metrics, should_run, info=None, traceroute=None) -> None:  # pragma: no cover — socket/thread
    """Boucle serveur HTTP jusqu'à ce que should_run() soit faux (frontière OS)."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    def dispatch(command):
        return submit(command, timeout)

    class Handler(BaseHTTPRequestHandler):
        def _handle(self, method):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length).decode() if length else ""
            status, payload = handle_request(
                method, self.path, self.headers, body, token, dispatch, metrics, info, traceroute
            )
            data = json.dumps(payload).encode()
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionError):
                # Le client a fermé la socket avant qu'on réponde (typique d'un /send/* qui
                # dépasse le timeout du client et part sur le chemin timeout worker). La
                # commande a bien été traitée ; il n'y a plus rien à écrire -> on ignore.
                log.info("client déconnecté avant la réponse (%s %s)", method, self.path)

        def do_GET(self):
            self._handle("GET")

        def do_POST(self):
            self._handle("POST")

        def log_message(self, *args):  # silence le logging par défaut
            pass

    server = ThreadingHTTPServer((host, port), Handler)
    server.timeout = 1
    log.info("API de contrôle sur %s:%s", host, port)
    while should_run():
        server.handle_request()
    server.server_close()
