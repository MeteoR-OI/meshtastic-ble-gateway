# Changelog

Toutes les évolutions notables. Format inspiré de [Keep a Changelog](https://keepachangelog.com/fr/) ;
versionnage [SemVer](https://semver.org/lang/fr/). Notes et artefacts détaillés :
[Releases GitHub](https://github.com/MeteoR-OI/meshtastic-ble-gateway/releases).

## [0.8.2] — 2026-07-12
### Corrigé
- **Voisins actifs** : les métriques de voisinage (`count`, `best_snr`, `max_distance_km`,
  `distinct_*`) ne comptaient QUE le 0-hop, mais balayaient toute la NodeDB — y compris des nodes
  entendus il y a longtemps, dont la **position périmée gonflait `max_distance_km`**. `metrics.neighbors()`
  filtre désormais À L'EXTRACTION sur `last_heard` récent (voisins **actifs**) : le filtre se propage
  à `count`/`best_snr`, aux voisins **stockés** (donc `distinct_1h/24h/total`) et à `max_distance_km`.
  Fenêtre = `max(MBG_MONITOR_INTERVAL, 3600 s)`, surchargeable par **`MBG_NEIGHBOR_ACTIVE_SECS`**.
  Un voisin sans `last_heard` est exclu (fraîcheur non prouvable). Note : les snapshots déjà stockés
  avant ce correctif (avec nodes périmés) restent dans la table `neighbors` → `distinct_24h/total`
  peut inclure de l'historique jusqu'à ce qu'il vieillisse.

## [0.8.1] — 2026-07-12
### Ajouté
- **Portée & voisinage** dans `GET /metrics` (bloc `neighbors`) — calcul/SQL sur des données
  DÉJÀ remontées, **aucune nouvelle op BLE** :
  - `max_distance_km` : distance (haversine, km arrondi 0,1) du voisin 0-hop le plus lointain dont
    on connaît la position ; calculée par la sonde, persistée (colonne `node_metrics.max_distance_km`,
    migration auto) ; `null` si la passerelle ou tous les voisins n'ont pas de position.
  - `distinct_1h` / `distinct_24h` / `distinct_total` : voisins distincts (`COUNT(DISTINCT node_id)`
    sur la table `neighbors`) sur 1 h / 24 h / tout l'historique.

## [0.8.0] — 2026-07-11
### Ajouté
- **Outil de provisionnement** `python -m mbg.provision` (`--inspect`/`--apply`) : lit/écrit la
  config MQTT + position du node par BLE (une seule transaction, retry BLE, gestion du reboot
  post-commit avec budget de reconnexion patient et **exit 2 = commité-mais-non-vérifié** ;
  sortie dure via `os._exit` pour ne jamais geler sur les threads non-daemon de bleak),
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
