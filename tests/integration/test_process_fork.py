# SPDX-License-Identifier: AGPL-3.0-or-later
"""Intégration : vrai sous-processus (fork) via process_backend.

Valide la mécanique OS que les unitaires simulent : Value partagé remontant du
worker, is_alive/kill/join/exitcode. Skippé si le fork n'est pas dispo.
"""
import multiprocessing
import time

import pytest

from mbg.config import Config
from mbg.process_backend import WorkerHandle, spawn_worker

_FORK = "fork" in multiprocessing.get_all_start_methods()


def _beater(config, counter):
    """Cible du fork : bat 3 fois puis se fige (attend d'être tué)."""
    for _ in range(3):
        with counter.get_lock():
            counter.value += 1
        time.sleep(0.05)
    time.sleep(30)


@pytest.mark.skipif(not _FORK, reason="start method 'fork' indisponible")
@pytest.mark.filterwarnings("ignore::DeprecationWarning")  # fork sous pytest multi-thread
def test_real_fork_heartbeats_then_killed():
    ctx = multiprocessing.get_context("fork")
    handle = spawn_worker(Config(), ctx, target=_beater)
    assert isinstance(handle, WorkerHandle)
    try:
        deadline = time.time() + 5
        while handle.beats() < 3 and time.time() < deadline:
            time.sleep(0.05)
        assert handle.beats() >= 3  # le compteur partagé remonte bien du sous-process
        assert handle.is_alive() is True
    finally:
        handle.kill()
        handle.join()
    assert handle.is_alive() is False
    assert handle.exitcode is not None  # process réellement terminé
