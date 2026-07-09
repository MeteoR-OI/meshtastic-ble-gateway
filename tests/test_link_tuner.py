# SPDX-License-Identifier: AGPL-3.0-or-later
from mbg.config import Config
from mbg.link_tuner import (
    build_lecup_argv,
    parse_handle,
    supervision_ok,
    tune_link,
)

NODE = "F9:98:08:73:85:AE"
CON_OK = (
    "Connections:\n"
    "    < LE F9:98:08:73:85:AE handle 64 state 1 lm MASTER AUTH ENCRYPT\n"
)


class R:
    """Imite subprocess.CompletedProcess (stdout/stderr/returncode)."""

    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def make_run(con=None, lecup=None, con_exc=None, lecup_exc=None):
    """Fabrique un faux `run` qui dispatche selon la sous-commande hcitool."""
    con = con if con is not None else R(stdout=CON_OK)
    lecup = lecup if lecup is not None else R(returncode=0)
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        if argv[1] == "con":
            if con_exc is not None:
                raise con_exc
            return con
        if lecup_exc is not None:
            raise lecup_exc
        return lecup

    run.calls = calls
    return run


def _cfg(timeout=6000):
    return Config(ble_address=NODE, ble_supervision_timeout_ms=timeout)


# --- fonctions pures -------------------------------------------------------

def test_supervision_ok_true_and_false():
    assert supervision_ok(6000) is True
    assert supervision_ok(50) is False  # 50 <= (1+0)*50*2 = 100


def test_parse_handle_found():
    assert parse_handle(CON_OK, NODE) == 64
    assert parse_handle(CON_OK, NODE.lower()) == 64  # insensible à la casse


def test_parse_handle_absent():
    assert parse_handle(CON_OK, "AA:BB:CC:DD:EE:FF") is None
    assert parse_handle("Connections:\n", NODE) is None


def test_parse_handle_line_without_handle_keyword():
    out = "    < LE F9:98:08:73:85:AE state 1 lm MASTER\n"  # MAC mais pas de 'handle'
    assert parse_handle(out, NODE) is None


def test_parse_handle_malformed_value():
    out = "    < LE F9:98:08:73:85:AE handle xx state 1\n"  # handle non entier
    assert parse_handle(out, NODE) is None


def test_parse_handle_keyword_at_end():
    out = "    < LE F9:98:08:73:85:AE handle\n"  # 'handle' sans valeur derrière
    assert parse_handle(out, NODE) is None


def test_build_lecup_argv_units():
    argv = build_lecup_argv(64, 6000)
    assert argv == [
        "hcitool", "lecup", "--handle", "64",
        "--min", "24",  # 30 ms / 1.25
        "--max", "40",  # 50 ms / 1.25
        "--latency", "0",
        "--timeout", "600",  # 6000 ms / 10
    ]


# --- tune_link : toutes les branches --------------------------------------

def test_tune_link_disabled_returns_false_without_calling():
    run = make_run()
    assert tune_link(_cfg(timeout=0), run=run) is False
    assert run.calls == []  # rien lancé quand désactivé


def test_tune_link_supervision_too_short():
    run = make_run()
    assert tune_link(_cfg(timeout=50), run=run) is False
    assert run.calls == []  # contrainte spec violée -> pas d'appel


def test_tune_link_hcitool_con_fails():
    run = make_run(con_exc=OSError("hcitool absent"))
    assert tune_link(_cfg(), run=run) is False
    assert len(run.calls) == 1  # seul `con` tenté


def test_tune_link_no_connection():
    run = make_run(con=R(stdout="Connections:\n"))  # node absent -> handle None
    assert tune_link(_cfg(), run=run) is False
    assert len(run.calls) == 1  # pas de lecup si pas de handle


def test_tune_link_lecup_raises():
    run = make_run(lecup_exc=PermissionError("EPERM"))
    assert tune_link(_cfg(), run=run) is False
    assert len(run.calls) == 2  # con puis lecup tentés


def test_tune_link_lecup_nonzero_returncode():
    run = make_run(lecup=R(returncode=1, stderr="Operation not permitted"))
    assert tune_link(_cfg(), run=run) is False


def test_tune_link_success():
    run = make_run()
    assert tune_link(_cfg(), run=run) is True
    assert run.calls[0] == ["hcitool", "con"]
    assert run.calls[1][:2] == ["hcitool", "lecup"]
    assert run.calls[1][-1] == "600"  # supervision timeout en unités 10 ms
