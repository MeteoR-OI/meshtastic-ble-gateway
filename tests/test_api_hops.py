# SPDX-License-Identifier: AGPL-3.0-or-later
"""GET /hops — contrat A (forme, bornes, erreurs). Frère strict de /packets, sans map de noms."""
from mbg.api import handle_request

TOKEN = "t"
HEADERS = {"X-API-Token": TOKEN}


class FakeMetrics:
    """Vue store côté API : seul `packet_hops_history` est exercé ici."""

    def __init__(self):
        self.calls = []

    def packet_hops_history(self, since, bin_seconds):
        self.calls.append((since, bin_seconds))
        return {
            "bin": bin_seconds,
            "rows": [[1783622100, 0, 128], [1783622100, 2, 7], [1783622100, -1, 3]],
        }


def _get(path, metrics=None):
    return handle_request("GET", path, HEADERS, "", TOKEN, lambda c: {}, metrics=metrics)


def test_hops_returns_contract_shape():
    metrics = FakeMetrics()
    status, body = _get("/hops?since=1783600000&bin=900", metrics)
    assert status == 200
    assert body == {
        "bin": 900,
        "rows": [[1783622100, 0, 128], [1783622100, 2, 7], [1783622100, -1, 3]],
    }
    assert "nodes" not in body  # pas de map de noms (dimension = entier fixe)
    assert metrics.calls == [(1783600000.0, 900)]


def test_hops_defaults_since_zero_and_bin_300():
    metrics = FakeMetrics()
    status, body = _get("/hops", metrics)
    assert status == 200
    assert metrics.calls == [(0.0, 300)]
    assert body["bin"] == 300  # bin réfléchi dans la réponse


def test_hops_requires_the_token_exactly_like_metrics():
    """`_authorized` garde TOUTES les méthodes, GET compris (token NON vide, sinon le test
    passerait à vide : compare_digest("","") est vrai). Miroir de /packets et /metrics."""
    for route in ("/hops", "/packets", "/metrics"):
        status, body = handle_request(
            "GET", route, {}, "", "vrai-token", lambda c: {}, metrics=FakeMetrics()
        )
        assert (status, body) == (401, {"ok": False, "error": "non autorisé"})
    # ...et avec l'en-tête, ça passe.
    ok, _ = handle_request(
        "GET", "/hops", {"X-API-Token": "vrai-token"}, "", "vrai-token",
        lambda c: {}, metrics=FakeMetrics(),
    )
    assert ok == 200


def test_hops_404_when_monitoring_off():
    # Miroir EXACT du 404 de /metrics et /packets (même message) quand le store est absent.
    assert _get("/hops", None) == (404, {"ok": False, "error": "monitoring désactivé"})


def test_hops_400_on_non_numeric_params():
    for path in ("/hops?since=hier", "/hops?bin=beaucoup"):
        assert _get(path, FakeMetrics()) == (400, {"ok": False, "error": "paramètres invalides"})


def test_hops_400_on_bin_out_of_bounds():
    for path in ("/hops?bin=59", "/hops?bin=86401", "/hops?bin=0", "/hops?bin=-900"):
        assert _get(path, FakeMetrics()) == (400, {"ok": False, "error": "paramètres invalides"})


def test_hops_accepts_bin_bounds():
    metrics = FakeMetrics()
    assert _get("/hops?bin=60", metrics)[0] == 200
    assert _get("/hops?bin=86400", metrics)[0] == 200
    assert metrics.calls == [(0.0, 60), (0.0, 86400)]


def test_hops_404_takes_precedence_over_bad_params():
    # Monitoring off + params pourris : le 404 prime (on ne peut rien servir de toute façon).
    assert _get("/hops?bin=1", None)[0] == 404
