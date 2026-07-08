# SPDX-License-Identifier: AGPL-3.0-or-later
"""Intégration API de contrôle : vrai fork (aller-retour commande) + vrai serveur HTTP."""
import json
import multiprocessing
import queue
import socket
import threading
import time
import urllib.error
import urllib.request

import pytest

from mbg.api import serve
from mbg.config import Config
from mbg.process_backend import spawn_worker

_FORK = "fork" in multiprocessing.get_all_start_methods()


def _echo_worker(config, counter, cmd_q, res_q):
    """Cible du fork : draine les commandes et renvoie un écho (mime le pump du worker)."""
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            cmd = cmd_q.get(timeout=0.1)
        except queue.Empty:
            continue
        res_q.put({"id": cmd["id"], "ok": True, "echo": cmd.get("type")})


@pytest.mark.skipif(not _FORK, reason="start method 'fork' indisponible")
@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_real_fork_command_roundtrip():
    ctx = multiprocessing.get_context("fork")
    handle = spawn_worker(Config(), ctx, target=_echo_worker)
    try:
        result = handle.submit({"type": "text"}, timeout=3)
        assert result["ok"] is True
        assert result["echo"] == "text"
    finally:
        handle.kill()
        handle.join()


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _get(url, token=None):
    req = urllib.request.Request(url)
    if token is not None:
        req.add_header("X-API-Token", token)
    return urllib.request.urlopen(req, timeout=2)


def test_real_http_server_auth():
    port = _free_port()
    stop = threading.Event()

    def submit(command, timeout):
        return {"ok": True, "echo": command}

    thread = threading.Thread(
        target=serve,
        args=("127.0.0.1", port, "tok", 1.0, submit, lambda: not stop.is_set()),
        daemon=True,
    )
    thread.start()
    try:
        # attendre que le serveur écoute
        base = f"http://127.0.0.1:{port}"
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                resp = _get(base + "/health", token="tok")
                break
            except urllib.error.URLError:
                time.sleep(0.1)
        else:
            pytest.fail("serveur HTTP non démarré")

        assert resp.status == 200
        assert json.loads(resp.read())["status"] == "up"

        # sans token -> 401
        with pytest.raises(urllib.error.HTTPError) as exc:
            _get(base + "/health")
        assert exc.value.code == 401
    finally:
        stop.set()
        thread.join(timeout=3)
