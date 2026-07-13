# CLAUDE.md — meshtastic-ble-gateway

Pont **BLE → MQTT** pour faire remonter un node Meshtastic **BT-only** dans
[MeshForge](https://github.com/Robin-Lune/meshforge). Hébergé sur un Raspberry Pi.

> Ce fichier reste **agnostique**. Le **contexte terrain concret** (stations, MAC, broker,
> accès, historique de validation) vit dans **`CLAUDE.local.md`** (non versionné, gitignoré).

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
  **`_kill` = SIGKILL + join PUIS `disconnect` bluez forcé du node (v0.6.1)** : un worker gelé
  ne ferme pas l'ACL → `bluetoothd` garde `Connected: yes` → le node cesse d'émettre → le
  respawn ne le retrouve pas (boucle ∞, churn observé en prod). Le teardown doit venir du
  superviseur ; défaut = `bluetoothctl disconnect <MAC>` **borné par `timeout` subprocess** (ne
  gèle jamais le superviseur ; plus sûr qu'un D-Bus in-process). Seam `disconnect` injectable.
- `systemd_notify.py` — `sd_notify` (watchdog, sans dépendance).
- `control.py` — `execute_command(iface, command)` : traduit une commande (text/telemetry/
  position/request_position/admin) en appel meshtastic. Ne lève jamais. Whitelist admin
  extensible. Pour `want_ack`, renvoie `packet_id` (le node corrèle l'ACK). **`position`**
  ré-émet TOUJOURS des coordonnées (override `{lat,lon,alt}` ou position fixe lue sur le node)
  — jamais 0,0, que le firmware adopterait comme position locale (écraserait la position fixe).
  **Requêtes distantes (V0.6)** : `telemetry` avec `dest` → `sendTelemetry(destinationId,
  wantResponse=True)` ; `request_position` → `sendPosition(dest, wantResponse=True)`. La réponse
  du node distant arrive **async** via le mesh → remonte en `[uplink]` MQTT (pas dans la réponse HTTP).
- **ACK radio (want_ack)** : `sendText(onResponse=…)` est **CASSÉ** en meshtastic BLE 2.7.10
  (le handler ne matche pas le requestId — prouvé py-spy/capture). ⇒ on ne s'y fie PAS :
  `node` s'abonne à `meshtastic.receive`, corrèle un `ROUTING_APP` entrant dont le
  `requestId` == l'id d'un paquet `want_ack` envoyé, et logue `[downlink] ACK … → reçu/échec`
  (+ timeout de repli). Broadcast = ACK implicite (ROUTING_APP from self), même chemin.
- **Traceroute (endpoint `POST /traceroute` + planificateur auto)** — `traceroute.py` : émission
  `TRACEROUTE_APP` (`RouteDiscovery` vide, `wantResponse`) via `iface.sendData` (qui **retourne** le
  paquet → `.id` = clé de corrélation ; `sendTraceRoute` ne le retourne pas, et son
  `waitForTraceRoute()` bloquant est PROSCRIT — il squatterait le thread). La réponse arrive dans la
  boucle `meshtastic.receive` déjà branchée (`node._handler_receive` la passe à
  `TracerouteCoordinator.on_packet`), corrélée par `requestId == id` **ET** `from == dest`, parsée
  (`decode_route` : chemin aller `[origin,*route,dest]` + retour si `snr_back`, SNR = firmware/4,
  sentinelle -128→None), puis **publiée MQTT** (topic `MBG_TRACEROUTE_TOPIC`) **+ écrite SQLite** ;
  un timer de repli produit un statut `timeout`. **Rien ne bloque le worker** : le mode `wait:true`
  de l'endpoint N'attend PAS côté worker (figerait le poll → SIGKILL) — l'API (superviseur) relit la
  ligne SQLite (WAL) via `api.TracerouteReader.wait`. Tout exécuté **dans le worker qui tient déjà le
  BLE** → jamais de 2ᵉ connexion, aucune coupure. Fonctions pures (`normalize_dest`, `decode_snr`,
  `decode_route`, `build_result`) + coordinateur à frontières injectées (send/publish/store/horloge/
  timer). `traceroute_scheduler.py` : `TracerouteScheduler.decide(now)` applique les garde-fous
  (min-gap global +jitter, min/nœud, budget quotidien reset minuit TZ, heures calmes, garde
  chan-util, BLE down) puis la **politique enfichable** (`static` round-robin / `staleness` = le node
  entendu récemment au dernier traceroute réussi le plus ancien ; registre `_POLICIES` extensible
  `recent`/`adaptive`) ; opt-in `MBG_TRACEROUTE_ENABLED` (défaut off), tout injecté (horloge/heure
  locale/RNG jitter). Le coordinateur est monté par le worker (`traceroute_setup` passé à
  `run_one_session`) dès que `config.traceroute_active` (= planificateur ON **ou** API ouverte —
  l'endpoint marche sans le planificateur) ; le store SQLite existe alors même si le monitoring est
  off (mais `/metrics` reste gouverné par le monitoring). SQLite : table `traceroute` (ISO pour
  l'affichage + `sent_epoch/recv_epoch` pour le calcul du planificateur) + requêtes d'état
  (`traceroute_last_sent`/`_last_attempt_by_node`/`_last_success_by_node`/`_count_since`/`_counters`).
  Voir `docs/traceroute.md`.
- `api.py` — `handle_request(...)` **pur** (auth token + routage POST downlink via `dispatch`
  + GET monitoring via `metrics`) + `serve(...)` (adaptateur `http.server`, pragma/intégration).
  API OPT-IN (token). GET `/health`, `/info` (version + identité node + config, pour la découverte),
  `/metrics` (+ compteurs traceroute si monitoring), `/history` (`?type=traceroute` → historique
  traceroute) ; POST `/send/*`, `/admin`, `/traceroute` (validation dest/hop_limit/timeout ;
  async 202 ou `wait:true` bloquant via `TracerouteReader`). `/info` reçoit un dict `info` statique
  (version+config) + fusionne l'identité node lue via `metrics.latest()` **et le statut
  onboarding** `broker`/`mqtt_proxy_ok`/`map_reporting` (CONTRACTS onboarding §3, consommé par
  weewx-mbg) — mêmes colonnes sonde, 0/1 SQLite → vrais booléens, `null` si monitoring off.
- **Monitoring / sonde (V0.3)** — `storage.py` : `MetricsStore` (SQLite stdlib, mode **WAL** →
  2 écrivains multi-process ; tables `node_metrics`/`neighbor_registry`/`link_quality` ; `record_node`,
  `upsert_neighbors`, `record_link`, `latest`, `history`, `prune`, `export_csv`). Connexion bornée par
  un context manager `_conn` (toujours fermée → pas de fuite). `metrics.py` : lecteurs **purs**
  (`node_metrics`, `node_identity` = id+longName du node local, `position`, `neighbors` = voisins
  actifs 0-hop **et** relayés avec SNR/RSSI/position/`hops_away`) depuis un fake iface. `node_metrics`
  stocke aussi `node_id`/`node_name` + le **statut MQTT** `mqtt_broker`/`mqtt_proxy_ok`/
  `mqtt_map_reporting` (lu de `localNode.moduleConfig.mqtt` par `metrics.mqtt_status`, sans I/O radio ;
  `mqtt_proxy_ok` = `enabled` ET `proxy_to_client_enabled` ; colonnes ajoutées par **migration auto**
  `ALTER TABLE` à l'init — les bases de prod pré-existantes survivent — dont `max_distance_km`, V0.8.1).
  **Voisinage = registre persistant (PORTÉE v2)** : `neighbor_registry(node_id PK, last_heard, lat,
  lon, snr, hops_away)`, **upsert** à chaque sonde (`INSERT OR REPLACE` des entendus, conservation des
  autres). `store.latest()` calcule TOUT le bloc `neighbors` dessus, filtré `last_heard >= now - W`
  (W = `MetricsStore(active_window=…)`, le superviseur passe `resolve_active_window(monitor_interval,
  neighbor_active_secs)`) : `count`/`best_snr`/`max_distance_km` (DIRECT `hops_away==0`)/
  `max_distance_hops_km` (MULTI-HOP `hops_away≥1`) sur le set actif ; `distinct_1h/24h/total` = nb de
  lignes du registre par fenêtre (`total` = tout ; le registre n'est PAS purgé). Le registre **survit
  aux reconnexions** (corrige le sous-comptage post-restart). `0.0` km valide (co-localisés). Au 1er
  démarrage v2, `_seed_registry_from_legacy` graine le registre depuis l'ancienne table snapshot
  `neighbors` (`INSERT OR IGNORE`, idempotent, jamais d'écrasement du live) pour préserver la
  continuité de `distinct_total` ; ensuite l'ancienne table est orpheline (plus écrite/lue).
  `node_metrics.max_distance_km` reste la série temporelle du DIRECT par sonde (calcul live, `/history`) —
  peut sous-estimer le `max_distance_km` de `/metrics` (registre accumulé) ; divergence assumée/documentée. **Voisins actifs (V0.8.2)** :
  `metrics.neighbors` filtre à l'extraction sur `last_heard >= now - W` (voisin sans `last_heard` =
  exclu) ; le worker calcule W (constante par session) + `now` (horloge injectée) et les passe à
  `read_metrics`. Le **worker**
  écrit node_metrics + upsert du registre voisins (monitor injecté dans `run_one_session`) : **un relevé tôt
  dans chaque session** (dès le lien établi) **puis** à la cadence `monitor_interval` — sinon,
  lien instable oblige (sessions < `monitor_interval`), le tic périodique ne tomberait jamais
  et node_metrics resterait vide (bug terrain 2026-07-08). Le **superviseur** écrit
  link_quality sur événement (compteur reconnexions, indépendant de la longévité de session) + thread
  d'export CSV/purge. Lecture batterie ACTIVE (`getMyNodeInfo`) → contourne le broadcast 12 h.
  **Pas de RSSI du lien BLE** : vérifié en prod (BlueZ 5.55), `bluetoothd` détient hci0 →
  ni HCI Read RSSI, ni mgmt Get Conn Info, ni D-Bus Device1.RSSI ne donnent de valeur sur un
  lien LE connecté (même en root) sans couper la passerelle. Le **signal de qualité BLE = le
  compteur de reconnexions** (`link_quality`).
- **Paliers batterie + duty-cycle (V0.4)** — `tiers.py` : constantes nommées (seuils 75/50/25 %,
  cadences 15/30/60 min ; **aucun magic number**) + `select_tier(level, current, hysteresis)` **pur**
  avec hystérésis **collante vers le haut** (descente au seuil nominal, remontée à seuil+hyst →
  anti-flapping). Opt-in (`MBG_BATTERY_TIERS`, défaut off) + nécessite le monitoring (source
  batterie) ; sinon `__main__` loggue un WARNING et désactive. Tout vit dans le **superviseur** :
  `_plan_tier()` (lit `store.latest()` batterie), `_effective_config()` (cadence du palier **qui
  écrase `MBG_MONITOR_INTERVAL`** — 15/30/60 min ; tiers ON = monitoring plus lent que le défaut 300 s ;
  `force_telemetry=True` **au changement de mode** → l'early-sample diffuse la batterie sur le
  mesh), et le **duty-cycle < 25 %** (fenêtre ON bornée à `duty_on` dans `_supervise`, puis
  `_wait(duty_off)`). ⚠️ **`_wait` est watchdog-friendly** : le OFF (>> `WatchdogSec`) doit
  continuer à pinger `WATCHDOG=1` sinon systemd tue le service (le backoff court, lui, reste un
  `sleep` simple : garder `max_reconnect_delay` < `WatchdogSec`). Le **seam `spawn` prend la config**
  effective par palier : `Callable[[Config], WorkerHandle]`. Duty-cycle = perte de flux **assumée**.
- **Stabilisation lien BLE (V0.5)** — `link_tuner.py` : sur signal faible (-80/-90 dBm) le churn
  vient d'un **supervision timeout** BLE (défaut BlueZ **420 ms** ; `reason 0x08`). Le node
  préférerait 2 s mais **le central (RPi) décide**, et **BlueZ 5.55 ignore la debugfs
  `supervision_timeout` en central** (bug #717, prouvé terrain via `btmon`). ⇒ on impose le timeout
  par une **`LE Connection Update` sur le lien vivant** (`hcitool lecup --timeout`), **une fois par
  session** (chaque connexion = une session worker ; le respawn couvre chaque reconnexion — pas de
  polling ni de suivi de handle). `tune_link` **ne lève jamais** (droits/hcitool/déconnexion logués).
  Fonctions **pures/testables** (`parse_handle`, `build_lecup_argv`, `supervision_ok`) + frontière OS
  `run=subprocess.run` injectable. Le worker construit le closure `tune` (si
  `ble_supervision_timeout_ms>0`) et le passe à `run_one_session` (appelé après `link.open()`).
  Opt-in ; **nécessite `CAP_NET_ADMIN`+`CAP_NET_RAW`** sur le service (émission HCI) — 2 lignes dans
  `mbg.service`, **pas** de service root séparé (garde le réglage dans le worker, testé à 100 %).
  Effet terrain : churn **~19-27/h → ~1,5/h** (compteur `link_quality`). Si lien < ~-95 dBm : passer
  à la **RF** (dongle USB antenne externe, ou firmware `NRF52_BLE_TX_POWER 8`).
- `__main__.py` — CLI. **L'ENV est la base de la config, la CLI override** (via
  `dataclasses.replace` : on n'override QUE les champs CLI → tout futur champ se propage seul,
  fin du bug « champ oublié »). Câble le superviseur avec `spawn_worker` + `get_context("fork")`
  + `_build_serve` (API si token) + le `MetricsStore` (si `monitor_interval > 0`).
- **Downlink** : API (thread du superviseur) → `Supervisor.submit` (worker connecté sinon
  503) → queue → worker → `link.send()` → `control.execute_command`. Un write qui gèle →
  worker SIGKILL (isolation). C'est le SEUL point qui rompt le « receive-only ».
- **Provisionnement (épic onboarding)** — `provision.py` : outil CLI **hors service**
  (`python -m mbg.provision --mac … --inspect|--apply`, mbg ARRÊTÉE : BLE = 1 client) qui
  lit/écrit `moduleConfig.mqtt` + `localConfig.position` + `uplink_enabled` du canal primaire.
  Sortie = JSON stable (CONTRACTS onboarding §2) sur stdout, logs stderr ; **exit 0 = vérifié,
  2 = commité-mais-non-vérifié** (`{applied:null, committed:true, verified:false, warning}` —
  le node reboote et peut mettre ~2 min à ré-annoncer : l'appelant traite 2 en succès provisoire
  et ré-inspecte ; JAMAIS l'enveloppe `{"error"}` pour ce cas), **1 = échec dur**. Écritures
  regroupées en **UNE transaction** (`beginSettingsTransaction` →
  `writeConfig` sections modifiées seulement → commit), **rien d'écrit si déjà conforme** (zéro
  reboot). Contraintes Phase 0 (T114 réel) : **retry connexion + backoff** (`connect_with_retry`),
  le node **REBOOTE au commit** → commit **fire-and-forget** (thread daemon + join court) puis
  reconnexion fraîche pour vérifier (`matches_target`) avec un **budget patient SÉPARÉ** du
  connect initial (`RECONNECT_*` : 10 essais / ≥150 s, `REBOOT_WAIT` 120 s — finding hw-test
  2026-07-11) ; l'iface pré-reboot n'est **jamais**
  fermée et tout `close()` passe par `_close_quietly` (thread + join borné — close() gèle sur
  lien mort). **L'entrée réelle `cli()` SORT via `os._exit`** (flush stdout d'abord) : une
  `BLEInterface` bleak/meshtastic laisse des threads **non-daemon** qui gèleraient un arrêt
  normal (`raise SystemExit` les joindrait) — hang confirmé au 2ᵉ hw-test T114 sur le chemin
  exit-2 ; même isolation que le worker de la passerelle. Testé : seam `terminate` injectable
  (unité) **+ subprocess réel sous timeout** avec thread non-daemon résiduel (`tests/hang_probe.py`
  — prouve la terminaison EFFECTIVE, pas juste la valeur de retour ; la couverture fake masquait
  le hang). Creds CLI absents = ceux du node conservés (§7.3). Tout injectable
  (factory/sleep/thread) → 100 % testé sans matériel ; `n'appaire PAS` (rôle installateur).

## Config : ENV = base, CLI = override

Le service systemd lance `python -m mbg` **sans argument** → tout vient de l'ENV (`MBG_*`).
Les arguments CLI ne servent qu'en usage manuel/PoC et priment s'ils sont fournis.
⚠️ Ne jamais reconstruire la config uniquement depuis argparse (bug historique : l'ENV
était ignorée, le service bouclait sur `localhost`).

## Tests & vérification (standard du projet)

- **100 % branch coverage** obligatoire (`pytest`, config dans `pyproject.toml`). Fakes
  dans `tests/fakes.py`. Le PoC (`poc/`) est exclu (spike matériel).
- Vérifier aussi en **Python 3.9** (cible RPi OS Bullseye), pas seulement en local :
  `docker run --rm -v "$PWD":/app -w /app python:3.9 bash -c "pip install -q -e '.[dev]' && pytest -q"`.
- **Plancher Python = 3.9** (`requires-python>=3.9`) car **meshtastic 2.7.x exige ≥3.9** (la
  dernière meshtastic supportant 3.7 est 2.3.11, trop ancienne). Sur **Raspbian 10 (Buster)**
  dont le python système est en 3.7, on **n'abaisse PAS** le code : on installe un Python 3.9+
  **isolé** (altinstall/pyenv, jamais le python système) — cf. `docs/installation.md`. Compat du
  userland Buster vérifiée en conteneur `python:3.9-buster` (deps + tests OK).
- **Buster = deux pièges BLE en plus (V0.6, validés en prod sur Buster)** : (1) **BlueZ 5.50 < 5.55**
  → **pas de notifications GATT** (connexion OK, zéro donnée) → installer le paquet vendorisé
  `bluez-meshforge` (BlueZ 5.55 sous `/opt`, `deploy/bluez-buster/`) + PATH du service vers
  `/opt/bluez-5.55/bin` (bleak lit `bluetoothctl --version`). (2) **bleak non pinné** (dep
  transitive) → bleak 3.x sur Python ≥3.10 **casse sur BlueZ <5.52** (`KeyError: 'Roles'`) →
  **`constraints.txt` pin `bleak==1.1.1`** (`pip install -e . -c constraints.txt`). Aussi : node
  **`Paired` mais NON `Trusted`** (sinon bluez auto-reconnecte → le node cesse d'émettre → scan KO).
- Intégration broker réel : `docker compose -f poc/docker-compose.yml up -d && pytest tests/integration --no-cov`.
- **La couverture NE prouve PAS la correction.** Toujours tester le **chemin de
  déploiement réel** (ex. `main([])` + env, comme systemd) et **smoker le vrai entrypoint**,
  pas seulement viser le 100 %. (Un bug de wiring ENV avait passé le 100 % via des tests CLI.)

## Roadmap

- **V0.1** (fait) : passerelle (forward opaque + résilience par isolation de process).
- **V0.2** (fait) : API de contrôle / downlink (texte, télémétrie, admin node).
- **V0.3** (fait) : monitoring — sonde SQLite (métriques node + qualité BLE), API + export CSV.
- **V0.4** (fait) : paliers batterie + duty-cycle du lien BLE (adaptatif ; opt-in ; seuils dans le README).
- **V0.5** (fait) : stabilisation du lien BLE sur signal faible — supervision timeout imposé au lien
  vivant via `hcitool lecup` (opt-in ; nécessite CAP_NET_ADMIN). Voir `link_tuner.py`.
- **V0.6** (fait) : support **Raspbian Buster** (Python/BlueZ 5.55 isolés, pin bleak, doc appairage/
  port) + **requêtes vers un node distant** (`/send/telemetry` avec `dest`, `/request/position`).
- **V0.7** (fait, côté mbg) : exposition de l'**identité du node** (id/longName dans `node_metrics`
  + agrégat voisins) et endpoint **`GET /info`** (version + identité + config) — brique côté
  passerelle de l'**intégration WeeWX** (extension `weewx-mbg` + tuile installer, autres repos).
- **V0.8** (fait) : **épic onboarding, phase gateway** — outil **`mbg.provision`** (config
  MQTT+position du node par BLE, contrat JSON pour l'installateur, sortie dure `os._exit` pour ne
  jamais geler) + **statut onboarding dans `/info`** (`broker`/`mqtt_proxy_ok`/`map_reporting`
  via la sonde). Contrats figés : `.agent-bus/CONTRACTS-onboarding.md` (hors repo). Voir
  `docs/provision.md`.
- **V0.8.1** (fait) : **portée & voisinage** dans `/metrics.neighbors` — `max_distance_km`
  (haversine passerelle↔voisins, colonne `node_metrics.max_distance_km`) + `distinct_1h/24h/total`
  (voisins distincts `COUNT(DISTINCT node_id)`). **Aucune nouvelle op BLE** (calcul/SQL sur le cache
  NodeDB + table `neighbors`). Contrat : `.agent-bus/CONTRACTS-portee.md §1`.
- **V0.8.2** (fait) : **voisins actifs + registre persistant + multi-hop** (PORTÉE v2). (a) filtre
  `last_heard` récent à l'extraction (fenêtre `max(monitor_interval, 3600 s)`, `MBG_NEIGHBOR_ACTIVE_SECS`)
  → plus de nodes périmés dans les métriques ; (b) **registre `neighbor_registry`** persistant (upsert
  par sonde) : le voisinage survit aux reconnexions (fini le sous-comptage post-restart) ; (c) 2ᵉ
  distance **`max_distance_hops_km`** (relayés) à côté de `max_distance_km` (direct). Contrat :
  `.agent-bus/CONTRACTS-portee.md §PORTÉE v2`.
- **Traceroute** (fait) : endpoint **`POST /traceroute`** (async 202 / `wait:true` bloquant via
  relecture SQLite) + **planificateur automatique** opt-in (`MBG_TRACEROUTE_ENABLED`, politiques
  `static`/`staleness`, garde-fous airtime). Émission + corrélation **dans le worker** (interface BLE
  vivante réutilisée → aucune coupure, pas de 2ᵉ client BLE). Résultat en MQTT (`MBG_TRACEROUTE_TOPIC`)
  + SQLite (`/history?type=traceroute`) + compteurs `/metrics`. Voir `traceroute.py`,
  `traceroute_scheduler.py`, `docs/traceroute.md`.
- **V0.9** : transports alternatifs (USB-série / WiFi-TCP) si le matériel du node le permet.

## Conventions

- En-tête `SPDX-License-Identifier: AGPL-3.0-or-later` sur chaque source.
- Commits : auteur = mainteneur (email GitHub-vérifié), **pas de trailer `Co-Authored-By`** ;
  citer Claude en prose (`Assisté par Claude Code (Anthropic).`).
- Ne pas modifier la config d'un node déjà en place — la **vérifier** en read-only.
- Secrets (creds MQTT) : uniquement dans le fichier systemd sur le RPi, jamais dans le repo.
