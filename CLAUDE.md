# CLAUDE.md — meshtastic-ble-gateway

Pont **BLE → MQTT** pour faire remonter un node Meshtastic **BT-only** dans
[MeshForge](https://github.com/Robin-Lune/meshforge). Hébergé sur un Raspberry Pi.

## Ce qui est vrai (ne pas re-débattre)

- **Mécanisme = MQTT Client Proxy over BLE**, validé empiriquement sur un T114 réel
  (le PoC dans `poc/`). Le node n'émet que du **`/e/` chiffré** (jamais de `/json/`,
  malgré `jsonEnabled=true`).
- La passerelle **forwarde le `/e/` opaque** (topic + payload tels quels). **Aucun
  déchiffrement, clé ou protobuf côté passerelle** — tout le crypto vit dans MeshForge
  (`MESHTASTIC_CHANNEL_KEYS`, `public_channels`). Ça garde le pont bête et robuste.
- API meshtastic-python : uplink = pubsub `meshtastic.mqttclientproxymessage`
  `(proxymessage, interface)` → republier `proxymessage.topic`/`.data` ; perte de lien =
  pubsub `meshtastic.connection.lost`.
- **meshtastic GÈLE sur lien mort, de façon non-tuable** : appels BLE sans timeout
  (`_sendDisconnect`, puis `disconnect()` via `async_await`→`future.result()` sans timeout ;
  confirmé py-spy). Impossible à récupérer en thread (on ne tue pas un thread bloqué en C) ;
  borner `async_await` **fuit un thread daemon + event loop + fd par décrochage** (fatal
  armv7 32-bit). ⇒ **Isolation de process** (voir Architecture) : le BLE tourne dans un
  worker jetable qu'on **SIGKILL**. Ne PAS re-tenter un fix in-process (whack-a-mole prouvé).
- Sonde de vivacité (`node.default_liveness` via `is_connected` BlueZ) + `connection.lost`
  servent au worker à détecter le drop et sortir vite (`os._exit`) ; c'est le superviseur
  (parent) qui respawn.
- BLE : **1 seul client connecté à la fois**. Cible = MAC sur Linux/BlueZ, nom/UUID sur macOS.

## Architecture (`src/mbg/`) — superviseur / worker

Tout le I/O externe est **injecté derrière des fabriques/paramètres** → testable sans
matériel ni vrai process. Deux processus : un **superviseur** (parent, jamais de BLE) et un
**worker jetable** (fait le BLE, SIGKILLable).

- `config.py` — `Config` (dataclass) + `from_env()` (`MBG_*`). Champs de tuning :
  `supervisor_tick`, `connect_grace`, `alive_timeout`, `reconnect_delay`/`max_reconnect_delay`.
  Monitoring : `db_path`, `monitor_interval` (0=off), `force_telemetry`, `dump_dir`,
  `dump_interval`, `retention_days`.
- `proxy.py` — `Proxy.on_proxy_message` : republie au broker, ne crashe jamais.
- `mqtt_publisher.py` — `PahoPublisher` (adaptateur paho, `client_factory` injectable).
- `node.py` — `MeshtasticNodeLink` : connexion BLE + pubsub (proxy + lost) + sonde
  `is_alive()`. Tout injectable.
- `session.py` — `run_one_session(...)` : UNE session (broker + BLE + boucle poll + sonde),
  émet un `heartbeat()` à chaque poll, rend la main au décrochage. **Ne ferme pas** (le
  worker `os._exit`). Réutilise proxy/publisher/node.
- `worker.py` — `_worker_body` (logique testable) + `run_worker` (frontière OS : `os._exit`,
  pragma) : le sous-processus. Sort en `os._exit` pour **ne jamais** appeler le `close()` qui gèle.
- `process_backend.py` — `WorkerHandle` (beats/is_alive/kill/join) + `spawn_worker(config, ctx)`
  (fork réel via `multiprocessing`). Seam injectable.
- `supervisor.py` — `Supervisor` : spawn worker → surveille heartbeat (phases connect/alive)
  → respawn si sorti / **SIGKILL** si figé → backoff plafonné + reset si connecté. Nourrit
  le watchdog systemd (`sd_notify`). Testé avec un faux spawn (aucun vrai process).
- `systemd_notify.py` — `sd_notify` (watchdog, sans dépendance).
- `control.py` — `execute_command(iface, command)` : traduit une commande (text/telemetry/
  position/admin) en appel meshtastic. Ne lève jamais. Whitelist admin extensible. Pour
  `want_ack`, renvoie `packet_id` (le node corrèle l'ACK). **`position`** ré-émet TOUJOURS
  des coordonnées (override `{lat,lon,alt}` ou position fixe lue sur le node) — jamais 0,0,
  que le firmware adopterait comme position locale (écraserait la position fixe).
- **ACK radio (want_ack)** : `sendText(onResponse=…)` est **CASSÉ** en meshtastic BLE 2.7.10
  (le handler ne matche pas le requestId — prouvé py-spy/capture). ⇒ on ne s'y fie PAS :
  `node` s'abonne à `meshtastic.receive`, corrèle un `ROUTING_APP` entrant dont le
  `requestId` == l'id d'un paquet `want_ack` envoyé, et logue `[downlink] ACK … → reçu/échec`
  (+ timeout de repli). Broadcast = ACK implicite (ROUTING_APP from self), même chemin.
- `api.py` — `handle_request(...)` **pur** (auth token + routage POST downlink via `dispatch`
  + GET monitoring via `metrics`) + `serve(...)` (adaptateur `http.server`, pragma/intégration).
  API OPT-IN (token). GET `/metrics`, `/history` lisent le store ; POST `/send/*`, `/admin`.
- **Monitoring / sonde (V0.3)** — `storage.py` : `MetricsStore` (SQLite stdlib, mode **WAL** →
  2 écrivains multi-process ; tables `node_metrics`/`neighbors`/`link_quality` ; `record_*`,
  `latest`, `history`, `prune`, `export_csv`). Connexion bornée par un context manager
  `_conn` (toujours fermée → pas de fuite). `metrics.py` : lecteurs **purs** (`node_metrics`,
  `position`, `neighbors` 0-hop, `ble_rssi` best-effort) depuis un fake iface. Le **worker**
  écrit node_metrics/neighbors (monitor injecté dans `run_one_session`, cadence
  `monitor_interval`) ; le **superviseur** écrit link_quality (compteur reconnexions) + thread
  d'export CSV/purge. Lecture batterie ACTIVE (`getMyNodeInfo`) → contourne le broadcast 12 h.
- `__main__.py` — CLI. **L'ENV est la base de la config, la CLI override** (via
  `dataclasses.replace` : on n'override QUE les champs CLI → tout futur champ se propage seul,
  fin du bug « champ oublié »). Câble le superviseur avec `spawn_worker` + `get_context("fork")`
  + `_build_serve` (API si token) + le `MetricsStore` (si `monitor_interval > 0`).
- **Downlink** : API (thread du superviseur) → `Supervisor.submit` (worker connecté sinon
  503) → queue → worker → `link.send()` → `control.execute_command`. Un write qui gèle →
  worker SIGKILL (isolation). C'est le SEUL point qui rompt le « receive-only ».

## Config : ENV = base, CLI = override

Le service systemd lance `python -m mbg` **sans argument** → tout vient de l'ENV (`MBG_*`).
Les arguments CLI ne servent qu'en usage manuel/PoC et priment s'ils sont fournis.
⚠️ Ne jamais reconstruire la config uniquement depuis argparse (bug historique : l'ENV
était ignorée, le service bouclait sur `localhost`).

## Tests & vérification (standard MeteoR-OI)

- **100 % branch coverage** obligatoire (`pytest`, config dans `pyproject.toml`). Fakes
  dans `tests/fakes.py`. Le PoC (`poc/`) est exclu (spike matériel).
- Vérifier aussi en **Python 3.9** (cible RPi OS Bullseye), pas seulement en local :
  `docker run --rm -v "$PWD":/app -w /app python:3.9 bash -c "pip install -q -e '.[dev]' && pytest -q"`.
- Intégration broker réel : `docker compose -f poc/docker-compose.yml up -d && pytest tests/integration --no-cov`.
- **La couverture NE prouve PAS la correction.** Toujours tester le **chemin de
  déploiement réel** (ex. `main([])` + env, comme systemd) et **smoker le vrai entrypoint**,
  pas seulement viser le 100 %. (Un bug de wiring ENV avait passé le 100 % via des tests CLI.)

## Roadmap

- **V0.1** (fait) : passerelle (forward opaque + résilience par isolation de process).
- **V0.2** (fait) : API de contrôle / downlink (texte, télémétrie, admin node).
- **V0.3** (fait) : monitoring — sonde SQLite (métriques node + qualité BLE), API + export CSV.
- **V0.4** : paliers batterie + duty-cycle du lien BLE (seuils dans le README).

## Conventions

- En-tête `SPDX-License-Identifier: AGPL-3.0-or-later` sur chaque source.
- Commits : auteur = mainteneur (email GitHub-vérifié), **pas de trailer `Co-Authored-By`** ;
  citer Claude en prose (`Assisté par Claude Code (Anthropic).`).
- Ne pas modifier la config d'un node déjà en place — la **vérifier** en read-only.
- Secrets (creds MQTT) : uniquement dans le fichier systemd sur le RPi, jamais dans le repo.
