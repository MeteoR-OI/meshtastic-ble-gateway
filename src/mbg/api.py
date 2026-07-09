# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Mickaël Hoareau — MeteoR-OI
"""API HTTP de contrôle (downlink) — stdlib, zéro dépendance.

`handle_request` est **pur** (auth token + routage + validation) → testable à
100 %. `serve` est l'adaptateur socket/thread (frontière OS, testé en intégration).
Auth par en-tête `X-API-Token`. Routes : POST /send/text, /send/telemetry,
/send/position, /admin ; GET /health, /metrics, /history. Les commandes POST sont
relayées au worker via `dispatch` (déjà borné en
timeout) qui renvoie `{"ok": bool, ...}`.
"""
from __future__ import annotations

import hmac
import json
import logging
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

log = logging.getLogger("mbg.api")


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
        return {"type": "telemetry", "dest": payload.get("dest")}
    if path == "/send/position":
        # lat/lon/alt optionnels : absents -> ré-émet la position FIXE du node (jamais 0,0).
        return {"type": "position", "lat": payload.get("lat"), "lon": payload.get("lon"), "alt": payload.get("alt")}
    if path == "/admin":
        return {"type": "admin", "setting": payload.get("setting"), "value": payload.get("value")}
    return None


def _status_for(result: Dict[str, Any]) -> int:
    if result.get("ok"):
        return 200
    err = result.get("error", "")
    if "aucun worker" in err:
        return 503
    if "timeout" in err:
        return 504
    return 400


def _handle_get(route: str, query: str, metrics) -> Tuple[int, Dict[str, Any]]:
    if route == "/health":
        return 200, {"ok": True, "status": "up"}
    if route in ("/metrics", "/history"):
        if metrics is None:
            return 404, {"ok": False, "error": "monitoring désactivé"}
        if route == "/metrics":
            return 200, metrics.latest()
        params = parse_qs(query)
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
) -> Tuple[int, Dict[str, Any]]:
    """Traite une requête (pur). `dispatch` relaie au worker ; `metrics` lit la base (GET)."""
    if not _authorized(headers, token):
        return 401, {"ok": False, "error": "non autorisé"}
    parsed = urlparse(path)
    if method == "GET":
        return _handle_get(parsed.path, parsed.query, metrics)
    if method == "POST":
        try:
            payload = json.loads(body) if body else {}
        except ValueError:
            return 400, {"ok": False, "error": "JSON invalide"}
        command = _command_for(parsed.path, payload)
        if command is None:
            return 404, {"ok": False, "error": "route inconnue"}
        result = dispatch(command)
        return _status_for(result), result
    return 404, {"ok": False, "error": "route inconnue"}


def serve(host, port, token, timeout, submit, metrics, should_run) -> None:  # pragma: no cover — socket/thread
    """Boucle serveur HTTP jusqu'à ce que should_run() soit faux (frontière OS)."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    def dispatch(command):
        return submit(command, timeout)

    class Handler(BaseHTTPRequestHandler):
        def _handle(self, method):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length).decode() if length else ""
            status, payload = handle_request(method, self.path, self.headers, body, token, dispatch, metrics)
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
