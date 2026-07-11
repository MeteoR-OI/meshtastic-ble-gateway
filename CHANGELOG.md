# Changelog

Toutes les évolutions notables. Format inspiré de [Keep a Changelog](https://keepachangelog.com/fr/) ;
versionnage [SemVer](https://semver.org/lang/fr/). Notes et artefacts détaillés :
[Releases GitHub](https://github.com/MeteoR-OI/meshtastic-ble-gateway/releases).

## [Non publié]
### Ajouté
- **Outil de provisionnement** `python -m mbg.provision` (`--inspect`/`--apply`) : lit/écrit la
  config MQTT + position du node par BLE (une seule transaction, retry BLE, gestion du reboot
  post-commit avec budget de reconnexion patient et **exit 2 = commité-mais-non-vérifié**),
  sortie JSON stable pour l'installateur — voir [docs/provision.md](docs/provision.md).
- **Statut d'onboarding dans `GET /info`** : `broker`, `mqtt_proxy_ok`, `map_reporting`, lus de la
  config MQTT du node par la sonde (colonnes `node_metrics.mqtt_*`, migration auto des bases
  existantes) — consommés par l'intégration WeeWX.

### Corrigé
- `pyproject.toml` réaligné sur la version `0.7.0` (resté à `0.6.1` lors du tag).

## [0.7.0] — 2026-07-10
### Ajouté
- **Identité du node local** dans le monitoring : `getMyNodeInfo()['user']` (id + nom humain)
  persisté en base (`node_metrics.node_id`/`node_name`) et exposé via `/metrics.node`.
- **Agrégat voisins** dans `GET /metrics` : `neighbors: {count, best_snr}` (dernier batch).
- **`GET /info`** (derrière token) : `{version, node_id, node_name, monitor_interval,
  battery_tiers}` — surface de découverte pour l'intégration WeeWX et la tuile de l'installateur.

Base de l'**intégration WeeWX** (extension `weewx-mbg`, skin, installateur — repos dédiés).

## [0.6.1] — 2026-07-09
### Corrigé
- **Fin du churn BLE** : le superviseur force un `bluetoothctl disconnect` (borné) après chaque
  SIGKILL d'un worker gelé — sinon `bluetoothd` gardait l'ACL, le node cessait d'émettre et le
  respawn ne le retrouvait pas (boucle `No peripheral found`). Reconnexion désormais automatique.

## [0.6.0] — 2026-07-09
### Ajouté
- **Requêtes vers un node distant** : `POST /send/telemetry` avec `dest` et `POST /request/position`
  (`wantResponse`) — la réponse du node distant remonte en `[uplink]` MQTT.
- **Support Raspberry Pi OS Buster** : Python 3.11 isolé (`/opt`), **BlueZ 5.55 vendorisé**
  (`bluez-meshforge`), pin `bleak==1.1.1` (`constraints.txt`), **artefacts pré-compilés** (Python
  `.deb` + wheelhouse armhf) pour une install hors-ligne sans compilation.

## [0.5.0] — 2026-07-09
### Ajouté
- **Stabilisation du lien BLE sur signal faible** (opt-in) : impose le supervision timeout au lien
  vivant via `hcitool lecup`, une fois par session — churn réduit d'~94 % en terrain.

## [0.4.0] — 2026-07-09
### Ajouté
- **Paliers batterie + duty-cycle** (opt-in) : cadence adaptative (15/30/60 min) selon la batterie
  du node, duty-cycle < 25 % (lien coupé pour laisser le node dormir), hystérésis anti-flapping,
  télémétrie diffusée au changement de mode.

## [0.3.0] — 2026-07-09
### Ajouté
- **Monitoring / sonde** : métriques node (batterie fraîche via lecture active) + qualité du lien
  BLE en SQLite (WAL), API `GET /metrics` & `/history`, export CSV + purge.
- `POST /send/position` : ré-émet la position fixe du node (jamais `0,0`).

## [0.2.0] — 2026-07-08
### Ajouté
- **API de contrôle / downlink** (opt-in, token) : `/send/text` (avec `want_ack`), `/send/telemetry`,
  `/admin`, `/health`. ACK radio corrélé via `meshtastic.receive` (contourne un bug BLE 2.7.10).

## [0.1.0] — 2026-07-07
### Ajouté
- **Passerelle durcie** : forward opaque du Client Proxy `/e/`, **résilience par isolation de
  process** (superviseur + worker jetable SIGKILLable), watchdog systemd, backoff de reconnexion.
- 100 % branch coverage, CI, Docker, service systemd. Plancher Python 3.9.

[0.6.1]: https://github.com/MeteoR-OI/meshtastic-ble-gateway/releases/tag/v0.6.1
[0.6.0]: https://github.com/MeteoR-OI/meshtastic-ble-gateway/releases/tag/v0.6.0
[0.5.0]: https://github.com/MeteoR-OI/meshtastic-ble-gateway/releases/tag/v0.5.0
[0.4.0]: https://github.com/MeteoR-OI/meshtastic-ble-gateway/releases/tag/v0.4.0
[0.3.0]: https://github.com/MeteoR-OI/meshtastic-ble-gateway/releases/tag/v0.3.0
[0.2.0]: https://github.com/MeteoR-OI/meshtastic-ble-gateway/releases/tag/v0.2.0
[0.1.0]: https://github.com/MeteoR-OI/meshtastic-ble-gateway/releases/tag/v0.1.0
