# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Mickaël Hoareau — MeteoR-OI
"""API HTTP de contrôle (downlink) — stdlib, zéro dépendance.

`handle_request` est **pur** (auth token + routage + validation) → testable à
100 %. `serve` est l'adaptateur socket/thread (frontière OS, testé en intégration).
Auth par en-tête `X-API-Token`. Routes : POST /send/text, /send/telemetry, /admin ;
GET /health. Les commandes sont relayées au worker via `dispatch` (déjà borné en
timeout) qui renvoie `{"ok": bool, ...}`.
"""
from __future__ import annotations

import hmac
import json
import logging
from typing import Any, Callable, Dict, Optional, Tuple

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
        }
    if path == "/send/telemetry":
        return {"type": "telemetry", "dest": payload.get("dest")}
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


def handle_request(
    method: str,
    path: str,
    headers,
    body: str,
    token: str,
    dispatch: Callable[[Dict[str, Any]], Dict[str, Any]],
) -> Tuple[int, Dict[str, Any]]:
    """Traite une requête (pur). `dispatch(command)` relaie au worker et renvoie le résultat."""
    if not _authorized(headers, token):
        return 401, {"ok": False, "error": "non autorisé"}
    if method == "GET" and path == "/health":
        return 200, {"ok": True, "status": "up"}
    if method == "POST":
        try:
            payload = json.loads(body) if body else {}
        except ValueError:
            return 400, {"ok": False, "error": "JSON invalide"}
        command = _command_for(path, payload)
        if command is None:
            return 404, {"ok": False, "error": "route inconnue"}
        result = dispatch(command)
        return _status_for(result), result
    return 404, {"ok": False, "error": "route inconnue"}


def serve(host, port, token, timeout, submit, should_run) -> None:  # pragma: no cover — socket/thread
    """Boucle serveur HTTP jusqu'à ce que should_run() soit faux (frontière OS)."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    def dispatch(command):
        return submit(command, timeout)

    class Handler(BaseHTTPRequestHandler):
        def _handle(self, method):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length).decode() if length else ""
            status, payload = handle_request(method, self.path, self.headers, body, token, dispatch)
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

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
