# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Mickaël Hoareau — MeteoR-OI
"""Adaptateur multiprocessing : lance le worker dans un vrai sous-processus.

`WorkerHandle` enveloppe un `multiprocessing.Process` + un compteur partagé
(`Value`) que le worker incrémente à chaque poll. Le superviseur ne voit que ce
handle (injectable → fakes en test). `spawn_worker` reçoit le contexte
(`get_context("fork")`) de l'appelant → testable sans vrai fork.
"""
from __future__ import annotations

from typing import Callable

from .config import Config
from .worker import run_worker


class WorkerHandle:
    """Vue superviseur d'un worker : heartbeat + cycle de vie."""

    def __init__(self, process, counter) -> None:
        self._process = process
        self._counter = counter

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


def spawn_worker(config: Config, context, *, target: Callable = run_worker) -> WorkerHandle:
    """Fork un worker via `context` (multiprocessing) et renvoie son handle."""
    counter = context.Value("L", 0)
    process = context.Process(target=target, args=(config, counter), name="mbg-worker", daemon=False)
    process.start()
    return WorkerHandle(process, counter)
