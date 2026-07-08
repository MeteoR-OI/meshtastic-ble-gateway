# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Mickaël Hoareau — MeteoR-OI
"""Adaptateur multiprocessing : lance le worker dans un vrai sous-processus.

`WorkerHandle` enveloppe un `multiprocessing.Process` + un compteur partagé
(`Value`, heartbeat) + deux queues (commandes descendantes / résultats remontants).
Le superviseur ne voit que ce handle (injectable → fakes en test). `spawn_worker`
reçoit le contexte (`get_context("fork")`) de l'appelant → testable sans vrai fork.
"""
from __future__ import annotations

import queue
import threading
from typing import Any, Callable, Dict

from .config import Config
from .worker import run_worker


class WorkerHandle:
    """Vue superviseur d'un worker : heartbeat, cycle de vie, envoi de commandes."""

    def __init__(self, process, counter, cmd_q=None, res_q=None) -> None:
        self._process = process
        self._counter = counter
        self._cmd_q = cmd_q
        self._res_q = res_q
        self._lock = threading.Lock()
        self._seq = 0

    def beats(self) -> int:
        return self._counter.value

    def is_alive(self) -> bool:
        return self._process.is_alive()

    def kill(self) -> None:
        self._process.kill()

    def join(self, timeout=None) -> None:
        self._process.join(timeout)

    @property
    def exitcode(self):
        return self._process.exitcode

    def submit(self, command: Dict[str, Any], timeout: float) -> Dict[str, Any]:
        """Envoie une commande au worker et attend son résultat (sérialisé, borné)."""
        with self._lock:
            # Purge d'éventuels résultats périmés (submit précédent timeouté).
            while True:
                try:
                    self._res_q.get_nowait()
                except queue.Empty:
                    break
            self._seq += 1
            self._cmd_q.put({**command, "id": self._seq})
            try:
                return self._res_q.get(timeout=timeout)
            except queue.Empty:
                return {"ok": False, "error": "timeout worker (commande non confirmée)"}


def spawn_worker(config: Config, context, *, target: Callable = run_worker) -> WorkerHandle:
    """Fork un worker via `context` (multiprocessing) et renvoie son handle."""
    counter = context.Value("L", 0)
    cmd_q = context.Queue()
    res_q = context.Queue()
    process = context.Process(
        target=target, args=(config, counter, cmd_q, res_q), name="mbg-worker", daemon=False
    )
    process.start()
    return WorkerHandle(process, counter, cmd_q, res_q)
