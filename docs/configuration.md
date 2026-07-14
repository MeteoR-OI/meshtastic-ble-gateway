# Configuration (variables d'environnement)

La passerelle se configure **entièrement par variables d'environnement `MBG_*`** — c'est ainsi
que le service systemd la paramètre. Les **arguments CLI** (`--ble`, `--broker`, `--port`,
`--username`, `--password`) ne servent qu'en usage manuel/PoC et **priment** s'ils sont fournis.

> **ENV = base, CLI = override.** Ne jamais reconstruire la config uniquement depuis la CLI
> (bug historique : l'ENV ignorée, le service bouclait sur `localhost`).

Exemples commentés prêts à l'emploi : [`deploy/mbg.service`](../deploy/mbg.service).

## Cœur — BLE & broker

| Variable | Défaut | Rôle |
|---|---|---|
| `MBG_BLE_ADDRESS` | *(voir service)* | MAC (Linux/BlueZ) ou nom/UUID (macOS) du node |
| `MBG_BROKER_HOST` | `localhost` | hôte du broker MQTT |
| `MBG_BROKER_PORT` | `1883` | port MQTT |
| `MBG_BROKER_USERNAME` / `MBG_BROKER_PASSWORD` | – | auth broker (**secret** : service uniquement) |

## Résilience — reconnexion & surveillance

| Variable | Défaut | Rôle |
|---|---|---|
| `MBG_RECONNECT_DELAY` | `5` | délai initial du backoff de respawn du worker (s) |
| `MBG_MAX_RECONNECT_DELAY` | `30` | plafond du backoff exponentiel (s). ⚠️ garder `< WatchdogSec` (120) |
| `MBG_POLL_INTERVAL` | `0.5` | cadence sonde/heartbeat du worker (s) |
| `MBG_SUPERVISOR_TICK` | `1` | cadence de surveillance du superviseur (s) |
| `MBG_CONNECT_GRACE` | `45` | délai toléré sans heartbeat **pendant** la connexion BLE (s) |
| `MBG_ALIVE_TIMEOUT` | `15` | gap max entre heartbeats une fois connecté (s) |

Détails : [resilience.md](resilience.md).

## API de contrôle (opt-in)

| Variable | Défaut | Rôle |
|---|---|---|
| `MBG_API_TOKEN` | – | **token** de l'API. Vide = **API désactivée** (défaut) |
| `MBG_API_HOST` | `0.0.0.0` | interface d'écoute (ex. `127.0.0.1` pour localhost) |
| `MBG_API_PORT` | `8080` | port de l'API. ⚠️ **`8080` peut entrer en conflit avec un nginx** → choisir un port libre (ex. `8791`) |
| `MBG_CONTROL_TIMEOUT` | `10` | attente max d'une réponse worker à une commande (s) |

Détails : [api.md](api.md).

## Monitoring / sonde (opt-in)

| Variable | Défaut | Rôle |
|---|---|---|
| `MBG_DB_PATH` | `metrics.db` | base SQLite (relative au `WorkingDirectory`) |
| `MBG_MONITOR_INTERVAL` | `300` | cadence de relevé des métriques node (s ; `0` = off). ⚠️ **ignoré si `MBG_BATTERY_TIERS` est actif** → la cadence suit alors le palier (15/30/60 min) |
| `MBG_MONITOR_FORCE_TELEMETRY` | – | `true` = `sendTelemetry` avant chaque relevé (mesure fraîche, coûte de l'airtime) |
| `MBG_NEIGHBOR_ACTIVE_SECS` | `0` | fenêtre « voisin actif » (s) : un voisin ne compte dans `/metrics.neighbors` que s'il a été entendu depuis moins longtemps. `0` = auto = `max(MBG_MONITOR_INTERVAL, 3600)` |
| `MBG_DUMP_DIR` | – | répertoire d'export CSV (vide = pas d'export) |
| `MBG_DUMP_INTERVAL` | `3600` | cadence export CSV + purge (s) |
| `MBG_RETENTION_DAYS` | `0` | purge des données au-delà de N jours (`0` = pas de purge) |

Détails : [monitoring.md](monitoring.md).

## Paliers batterie + duty-cycle (opt-in)

| Variable | Défaut | Rôle |
|---|---|---|
| `MBG_BATTERY_TIERS` | – | `true` = cadence adaptative + duty-cycle selon la batterie (**nécessite le monitoring**). **Remplace `MBG_MONITOR_INTERVAL`** par la cadence du palier (15/30/60 min) |
| `MBG_DUTY_ON` | `300` | palier < 25 % : durée de la fenêtre de connexion (s) |
| `MBG_DUTY_OFF` | `1800` | palier < 25 % : durée de déconnexion entre fenêtres (s) |
| `MBG_TIER_HYSTERESIS` | `3` | marge (%) anti-flapping entre paliers |

Détails : [battery-tiers.md](battery-tiers.md).

## Stabilisation du lien BLE (opt-in)

| Variable | Défaut | Rôle |
|---|---|---|
| `MBG_BLE_SUPERVISION_TIMEOUT_MS` | `0` | `>0` (ex. `6000`) = supervision timeout (ms) imposé au lien via `hcitool lecup` à chaque session. **Nécessite `CAP_NET_ADMIN`+`CAP_NET_RAW` + `hcitool`** |
| `MBG_BLE_RECONCILE` | – | `true` = réconciliation bluez **avant chaque spawn** de worker : si le node est resté `Connected` (ACL résiduel d'un worker SIGKILL / stop mal fermé), force un `disconnect` pour qu'il ré-émette → scan rapide au lieu de geler `connect_grace` s. N'appaire/désappaire jamais. **Recommandé sur RPi** (fiabilise restart/respawn). Nécessite `bluetoothctl` dans le PATH |
| `MBG_BLE_SETTLE` | `3` | délai (s) après un `disconnect` de réconciliation, le temps que le node ré-émette ses advertisements |

Détails et prérequis capabilities : [resilience.md](resilience.md#stabilisation-du-lien-ble-signal-faible).

## Traceroute (endpoint + planificateur)

L'endpoint `POST /traceroute` est actif dès que l'API l'est (`MBG_API_TOKEN`). Le **planificateur
automatique** est opt-in (`MBG_TRACEROUTE_ENABLED`). Défauts conservateurs (parcimonie airtime).

| Variable | Défaut | Rôle |
|---|---|---|
| `MBG_TRACEROUTE_ENABLED` | – | `true` = active le **planificateur** (endpoint indépendant) |
| `MBG_TRACEROUTE_POLICY` | `staleness` | `static` \| `staleness` |
| `MBG_TRACEROUTE_DAILY_BUDGET` | `6` | nb max de traceroute auto / jour |
| `MBG_TRACEROUTE_HOP_LIMIT` | `7` | hop_limit par défaut (borné [1..7]) |
| `MBG_TRACEROUTE_TARGETS` | – | `!hex,!hex` (**requis si** `policy=static`) |
| `MBG_TRACEROUTE_RECENT_H` | `24` | `staleness` : fenêtre « entendu récemment » (h) |
| `MBG_TRACEROUTE_PER_NODE_MIN_S` | `21600` | intervalle min par nœud (6 h) |
| `MBG_TRACEROUTE_MIN_GAP_S` | `900` | intervalle min global (15 min) |
| `MBG_TRACEROUTE_QUIET_HOURS` | `22:00-06:00` | plage sans émission (fuseau station ; vide = aucune) |
| `MBG_TRACEROUTE_MAX_CHANUTIL` | `40` | skip le tick si `channel_utilization` local dépasse (%) |
| `MBG_TRACEROUTE_PRIORITY` | – | `staleness` : nœuds prioritaires (facultatif) |
| `MBG_TRACEROUTE_TICK_S` | `300` | période d'évaluation du planificateur (s) |
| `MBG_TRACEROUTE_TOPIC` | `mbg/traceroute` | topic MQTT du résultat. ⚠️ broker MeshForge (ACL `msh/#`) : surcharger vers `msh/<région>/mbg/traceroute/!<nodeid>` sinon publish `Not authorized` (cf. [traceroute.md](traceroute.md)) |

Détails, format du résultat et exemples `curl` : [traceroute.md](traceroute.md).
