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
- **Le BLE décroche souvent EN SILENCE** (ni exception ni `connection.lost`). Le seul
  signal fiable = l'état BlueZ via `iface.client.bleak_client.is_connected` (bleak) — d'où
  la **sonde de vivacité** (`node.default_liveness`) sondée à chaque poll par le runner.
  Compléter par un watchdog systemd (`Type=notify`/`WatchdogSec`, module `systemd_notify`).
- **Anti-gel `meshtastic_patch` (crucial)** : `MeshInterface.close()` gèle sur lien mort en
  écrivant un paquet disconnect (`_sendDisconnect`→`write_gatt_char` sans timeout ; cause
  confirmée py-spy, 72% des dumps). On le **neutralise** (`apply_meshtastic_patches()` appelé
  dans `__main__`) — sans lui, la reconnexion in-process ne va jamais au bout. Patch défensif
  (idempotent, no-op si l'API meshtastic change). NB : la passerelle est receive-only, donc
  ce paquet disconnect ne sert à rien.
- BLE : **1 seul client connecté à la fois**. Cible = MAC sur Linux/BlueZ, nom/UUID sur macOS.

## Architecture (`src/mbg/`)

Tout le I/O externe est **injecté derrière des fabriques/paramètres** → testable sans matériel.

- `config.py` — `Config` (dataclass) + `from_env()` (variables `MBG_*`).
- `proxy.py` — `Proxy.on_proxy_message` : republie au broker, ne crashe jamais.
- `mqtt_publisher.py` — `PahoPublisher` (adaptateur paho, `client_factory` injectable).
- `node.py` — `MeshtasticNodeLink` : connexion BLE + abonnements pubsub (proxy + lost),
  `interface_factory`/`subscribe`/`unsubscribe` injectables.
- `runner.py` — `Gateway` : boucle de session + reconnexion (backoff `reconnect_delay`) ;
  `ConnectionLost` (armé par `connection.lost`) relance la session.
- `__main__.py` — CLI. **L'ENV est la base de la config, la CLI override.**

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

- **V0.1** (fait) : passerelle (forward opaque + résilience BLE).
- **V0.2** : monitoring — stockage SQLite local des infos node (base de la « sonde »).
- **V0.3** : paliers batterie + duty-cycle du lien BLE (seuils dans le README).

## Conventions

- En-tête `SPDX-License-Identifier: AGPL-3.0-or-later` sur chaque source.
- Commits : auteur = mainteneur (email GitHub-vérifié), **pas de trailer `Co-Authored-By`** ;
  citer Claude en prose (`Assisté par Claude Code (Anthropic).`).
- Ne pas modifier la config d'un node déjà en place — la **vérifier** en read-only.
- Secrets (creds MQTT) : uniquement dans le fichier systemd sur le RPi, jamais dans le repo.
