# SPDX-License-Identifier: AGPL-3.0-or-later
"""Flush des compteurs de paquets à la sortie de session (lien perdu).

Sans lui, une session plus courte que `monitor_interval` perdrait TOUS ses comptages : le
`monitor` ne relève qu'une fois tôt (compteurs vides) puis à la cadence — le tic périodique ne
tombe alors jamais. C'est le bug terrain du 2026-07-08 (node_metrics vide), rejoué.
"""
from fakes import FakeNodeLink, FakePublisher
from mbg.config import Config
from mbg.session import run_one_session


def seq(values):
    it = iter(values)
    return lambda: next(it)


def _run(box, flush, monitor=None, config=None, drop_after_polls=1):
    def nf(addr, cb, on_lost):
        box["link"] = FakeNodeLink(addr, cb)
        return box["link"]

    polls = {"n": 0}

    def sleep_(s):
        polls["n"] += 1
        if polls["n"] >= drop_after_polls:
            box["link"].alive = False  # décrochage silencieux

    return run_one_session(
        config or Config(poll_interval=0.5), lambda: FakePublisher(), nf,
        lambda: None, lambda: True, sleep=sleep_, monitor=monitor, flush=flush,
    )


def test_flush_called_with_link_on_link_loss():
    box = {}
    flushed = []
    _run(box, flush=flushed.append)
    assert flushed == [box["link"]]  # vidé une fois, à la sortie, avec le lien


def test_short_session_still_flushes_without_any_monitor_tick():
    # Le scénario terrain : session (2 polls) << monitor_interval -> le tic périodique ne tombe
    # jamais, seul l'early-sample a lieu (compteurs vides). Le flush de sortie sauve les comptages.
    box = {}
    flushed = []
    monitored = []
    _run(
        box, flush=flushed.append, monitor=monitored.append,
        config=Config(poll_interval=0.5, monitor_interval=300), drop_after_polls=2,
    )
    assert len(monitored) == 1  # uniquement l'early-sample (dict vide à ce moment-là)
    assert flushed == [box["link"]]  # ...et pourtant les compteurs sont bien vidés


def test_flush_failure_never_blocks_session_exit():
    # Un flush qui lève ne doit ni empêcher la sortie de session ni retarder le respawn.
    box = {}

    def boom(link):
        raise RuntimeError("base verrouillée")

    assert _run(box, flush=boom) == 0  # la session rend la main normalement


def test_no_flush_seam_is_fine():
    # Monitoring off -> pas de flush injecté : la session doit tourner sans.
    box = {}
    assert _run(box, flush=None) == 0
