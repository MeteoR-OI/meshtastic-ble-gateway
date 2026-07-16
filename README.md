<div align="center">

# 🌉 meshtastic-ble-gateway

**Pont BLE → MQTT pour nodes Meshtastic BT-only, à destination de [MeshForge](https://github.com/Robin-Lune/meshforge).**

[![CI](https://github.com/MeteoR-OI/meshtastic-ble-gateway/actions/workflows/ci.yml/badge.svg)](https://github.com/MeteoR-OI/meshtastic-ble-gateway/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/MeteoR-OI/meshtastic-ble-gateway)](https://github.com/MeteoR-OI/meshtastic-ble-gateway/releases)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org/)
[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-green)](LICENSE)

</div>

---

## Pourquoi ?

MeshForge n'ingère que du **MQTT**. Un node Meshtastic **Bluetooth-only** (ex. Heltec T114) ne peut
pas publier tout seul (pas de WiFi). Ce pont, hébergé sur un Raspberry Pi, se connecte au node en
**BLE** et relaie son trafic vers le broker Mosquitto que MeshForge consomme.

```
[Node BT-only] ──BLE──▶ [RPi : meshtastic-ble-gateway] ──MQTT──▶ Mosquitto ──▶ MeshForge
```

## Sommaire

- [Fonctionnalités](#fonctionnalités)
- [Démarrage rapide](#démarrage-rapide)
- [Documentation](#documentation)
- [Roadmap](#roadmap)
- [Environnements testés](#environnements-testés)
- [Mécanisme retenu](#mécanisme-retenu)
- [Développement](#développement)
- [Licence](#licence)

## Fonctionnalités

- **Proxy BLE → MQTT** : forward du Client Proxy `/e/` **opaque** (aucun crypto côté passerelle) —
  voir [architecture](docs/architecture.md).
- **Résilience** : isolation de process (superviseur + worker jetable SIGKILLable), watchdog
  systemd, disconnect bluez après gel — voir [resilience](docs/resilience.md).
- **API de contrôle** (opt-in) : texte, télémétrie, position, **requêtes vers un node distant**,
  admin — voir [api](docs/api.md).
- **Traceroute** : endpoint `POST /traceroute` (async ou bloquant) + **planificateur automatique**
  (opt-in, budget/politique) — sans coupure BLE — voir [traceroute](docs/traceroute.md).
- **Monitoring / sonde** : métriques node + qualité lien en SQLite, API + export CSV —
  voir [monitoring](docs/monitoring.md).
- **Paquets par nœud** : histogramme « paquets reçus par nœud, par tranche » (`GET /packets`,
  agrégation SQL, rétention 35 j) —
  voir [monitoring](docs/monitoring.md#paquets-reçus-par-nœud-get-packets).
- **Paliers batterie + duty-cycle** : adaptatif selon la batterie du node —
  voir [battery-tiers](docs/battery-tiers.md).
- **Stabilisation du lien** sur signal faible (`hcitool lecup`) — voir [resilience](docs/resilience.md#stabilisation-du-lien-ble-signal-faible).
- **Support Raspberry Pi OS Buster** (Python & BlueZ isolés, artefacts pré-compilés) —
  voir [installation](docs/installation.md#cas-raspbian-buster).

## Démarrage rapide

```bash
# dev / usage manuel
python -m venv .venv && ./.venv/bin/pip install -e ".[dev]"

# via CLI (macOS : nom BLE ; Linux : MAC) :
./.venv/bin/python -m mbg --ble <nom-ou-MAC-BLE> --broker localhost

# via l'environnement, comme le service systemd (aucun argument) :
MBG_BLE_ADDRESS=<MAC-du-node> MBG_BROKER_HOST=mqtt.example.org ./.venv/bin/python -m mbg
```

Déploiement RPi (systemd), y compris le cas **Buster** : **[docs/installation.md](docs/installation.md)**.

## Documentation

| Doc | Contenu |
|---|---|
| [installation.md](docs/installation.md) | Déploiement RPi (Bullseye/Buster), systemd, MeshForge, mise à jour |
| [configuration.md](docs/configuration.md) | **Toutes** les variables d'environnement `MBG_*` |
| [api.md](docs/api.md) | API de contrôle : endpoints, `curl`, requêtes distantes, codes |
| [traceroute.md](docs/traceroute.md) | Traceroute : endpoint `POST /traceroute`, planificateur, format MQTT/`/history` |
| [monitoring.md](docs/monitoring.md) | Sonde SQLite, `/metrics`, `/history`, `/packets` (paquets par nœud), export CSV |
| [battery-tiers.md](docs/battery-tiers.md) | Paliers batterie + duty-cycle |
| [resilience.md](docs/resilience.md) | Isolation de process, watchdog, tuning du lien BLE |
| [architecture.md](docs/architecture.md) | Composants, modules, flux uplink/downlink |
| [tested-environments.md](docs/tested-environments.md) | Matrice OS / Python / BlueZ / dépendances |
| [troubleshooting.md](docs/troubleshooting.md) | Dépannage & caveats |

## Roadmap

| Version | Contenu | État |
|---------|---------|------|
| **V0.1** | Passerelle durcie (isolation de process, tests 100 %, CI, systemd) | ✅ |
| **V0.2** | API de contrôle / downlink (texte, télémétrie, admin) | ✅ |
| **V0.3** | Monitoring : sonde SQLite + API + export CSV | ✅ |
| **V0.4** | Paliers batterie + duty-cycle (adaptatif) | ✅ |
| **V0.5** | Stabilisation du lien BLE sur signal faible | ✅ |
| **V0.6** | Support Raspberry Pi OS Buster + requêtes vers un node distant | ✅ |
| **V0.7** | Exposition identité node + `/info` (support de l'intégration WeeWX) | ✅ |
| **V0.8** | Épic onboarding : outil `mbg.provision` (config node par BLE) + statut onboarding `/info` (broker / `mqtt_proxy_ok` / `map_reporting`) | ✅ |
| **V0.9** | Traceroute : endpoint `POST /traceroute` + planificateur automatique (opt-in) ; réconciliation BLE au restart | ✅ |
| **V0.9.2** | Histogramme « paquets reçus par nœud » : `GET /packets` (agrégation SQL, rétention 35 j) | ✅ |
| **V0.10** | Transports alternatifs (USB-série / WiFi-TCP) | ⏳ |

Historique détaillé : [CHANGELOG.md](CHANGELOG.md).

## Environnements testés

Déployé en production sur Raspberry Pi OS **Bullseye** (Python 3.9) et **Buster** (Python 3.11
isolé + BlueZ 5.55). BlueZ **≥ 5.55 requis** ; `bleak` pinné en **1.1.1**. Matrice complète :
**[docs/tested-environments.md](docs/tested-environments.md)**.

## Mécanisme retenu

**Client Proxy firmware** (validé par le PoC sur un node réel) : le node produit ses trames MQTT et
les pousse sur le lien BLE ; la passerelle les **relaie telles quelles** au broker. Minimal et
robuste — aucun déchiffrement ni reconstruction côté passerelle. Valider sur ton node :
**[poc/README.md](poc/README.md)**.

## Développement

```bash
pytest                              # unitaires, 100 % branch coverage
docker compose -f poc/docker-compose.yml up -d && pytest tests/integration --no-cov
```

Conventions, tests et contexte interne : **[CONTRIBUTING.md](CONTRIBUTING.md)** · [CLAUDE.md](CLAUDE.md).

## Licence

[AGPL-3.0-or-later](LICENSE) — cohérent avec l'écosystème MeshForge.
