# SPDX-License-Identifier: AGPL-3.0-or-later
"""Garde anti-régression sur l'identité syslog de l'unité systemd.

Chantier log-identity : le service doit journaliser sous un tag `app_name` **stable
et non générique** (`meteor-mbg`), sinon il se confond avec les autres services Python
de la station dans VictoriaLogs. On vérifie la **source** de l'identité : le
`SyslogIdentifier` de la section `[Service]` de `deploy/mbg.service`.

Ce test ne parse PAS avec `configparser` : une unité systemd autorise les clés en
double (plusieurs `Environment=`, `Wants=`…), ce que `configparser` rejette. On lit
donc les paires clé/valeur d'une section à la main.
"""
from pathlib import Path

import pytest

SERVICE_FILE = Path(__file__).resolve().parents[1] / "deploy" / "mbg.service"

# Tags posés par le basename de l'interpréteur quand aucune identité n'est fixée.
GENERIC_TAGS = {"", "python", "python3"}


def _section_values(text, section, key):
    """Valeurs (dans l'ordre) de `key` dans `[section]` d'une unité systemd."""
    values = []
    current = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
            continue
        if current == section and "=" in line:
            name, value = line.split("=", 1)
            if name.strip() == key:
                values.append(value.strip())
    return values


@pytest.fixture(scope="module")
def service_text():
    return SERVICE_FILE.read_text(encoding="utf-8")


def test_service_declares_single_syslog_identifier(service_text):
    """Exactement un `SyslogIdentifier` dans `[Service]` (source d'identité unique)."""
    values = _section_values(service_text, "Service", "SyslogIdentifier")
    assert len(values) == 1, f"attendu 1 SyslogIdentifier, trouvé {values!r}"


def test_service_syslog_identifier_is_not_generic(service_text):
    """Le tag ne doit pas retomber sur le générique `python`/`python3`."""
    (value,) = _section_values(service_text, "Service", "SyslogIdentifier")
    assert value not in GENERIC_TAGS, f"tag générique interdit : {value!r}"


def test_service_syslog_identifier_matches_registry(service_text):
    """Valeur figée du registre log-identity (§1) : `meteor-mbg`."""
    (value,) = _section_values(service_text, "Service", "SyslogIdentifier")
    assert value == "meteor-mbg"
