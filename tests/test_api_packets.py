# SPDX-License-Identifier: AGPL-3.0-or-later
"""GET /packets — contrat A (forme, bornes, erreurs)."""
from mbg.api import handle_request

TOKEN = "t"
HEADERS = {"X-API-Token": TOKEN}


class FakeMetrics:
    """Vue store côté API : seul `packet_history` est exercé ici."""

    def __init__(self):
        self.calls = []

    def packet_history(self, since, bin_seconds):
        self.calls.append((since, bin_seconds))
        return {
            "bin": bin_seconds,
            "nodes": {"!a4f2c1b0": "Piton Maïdo", "!1f30c7d2": "Relais Tampon"},
            "rows": [[1783622100, "!a4f2c1b0", 42], [1783622100, "!1f30c7d2", 7]],
        }


def _get(path, metrics=None):
    return handle_request("GET", path, HEADERS, "", TOKEN, lambda c: {}, metrics=metrics)


def test_packets_returns_contract_shape():
    metrics = FakeMetrics()
    status, body = _get("/packets?since=1783600000&bin=900", metrics)
    assert status == 200
    assert body == {
        "bin": 900,
        "nodes": {"!a4f2c1b0": "Piton Maïdo", "!1f30c7d2": "Relais Tampon"},
        "rows": [[1783622100, "!a4f2c1b0", 42], [1783622100, "!1f30c7d2", 7]],
    }
    assert metrics.calls == [(1783600000.0, 900)]


def test_packets_defaults_since_zero_and_bin_300():
    metrics = FakeMetrics()
    status, body = _get("/packets", metrics)
    assert status == 200
    assert metrics.calls == [(0.0, 300)]
    assert body["bin"] == 300  # bin réfléchi dans la réponse


def test_packets_requires_the_token_exactly_like_metrics():
    """`_authorized` garde TOUTES les méthodes, pas seulement les POST.

    Le contrat annonçait « non authentifié, comme /metrics et /info » : les deux moitiés de la
    phrase sont incompatibles, car /metrics et /info sont eux-mêmes authentifiés (401 sans
    en-tête). /packets s'aligne donc sur /metrics — c'est le comportement RÉEL, vérifié sur le
    vrai serveur HTTP (un test à `token=""` passerait à vide : compare_digest("","") est vrai).
    """
    for route in ("/packets", "/metrics"):
        status, body = handle_request(
            "GET", route, {}, "", "vrai-token", lambda c: {}, metrics=FakeMetrics()
        )
        assert (status, body) == (401, {"ok": False, "error": "non autorisé"})
    # ...et avec l'en-tête, ça passe.
    ok, _ = handle_request(
        "GET", "/packets", {"X-API-Token": "vrai-token"}, "", "vrai-token",
        lambda c: {}, metrics=FakeMetrics(),
    )
    assert ok == 200


def test_packets_404_when_monitoring_off():
    # Miroir EXACT du 404 de /metrics (même message) quand le store est absent.
    assert _get("/packets", None) == (404, {"ok": False, "error": "monitoring désactivé"})


def test_packets_400_on_non_numeric_params():
    for path in ("/packets?since=hier", "/packets?bin=beaucoup"):
        assert _get(path, FakeMetrics()) == (400, {"ok": False, "error": "paramètres invalides"})


def test_packets_400_on_bin_out_of_bounds():
    for path in ("/packets?bin=59", "/packets?bin=86401", "/packets?bin=0", "/packets?bin=-900"):
        assert _get(path, FakeMetrics()) == (400, {"ok": False, "error": "paramètres invalides"})


def test_packets_accepts_bin_bounds():
    metrics = FakeMetrics()
    assert _get("/packets?bin=60", metrics)[0] == 200
    assert _get("/packets?bin=86400", metrics)[0] == 200
    assert metrics.calls == [(0.0, 60), (0.0, 86400)]


def test_packets_404_takes_precedence_over_bad_params():
    # Monitoring off + params pourris : le 404 prime (on ne peut rien servir de toute façon).
    assert _get("/packets?bin=1", None)[0] == 404
