# Architecture

Vue d'ensemble des composants et du flux. Pour le détail interne (invariants, seams de test,
décisions verrouillées), voir [`CLAUDE.md`](../CLAUDE.md).

## Composants

```
                    ┌─────────────────────────────────────────────┐
                    │  SUPERVISEUR (process parent, jamais de BLE) │
                    │  • surveille le heartbeat du worker          │
                    │  • respawn / SIGKILL + disconnect bluez      │
                    │  • watchdog systemd, paliers batterie        │
                    │  • API de contrôle (thread) + export CSV     │
                    └───────────────┬─────────────────────────────┘
                        spawn/kill  │  heartbeat + queues (commandes)
                    ┌───────────────▼─────────────────────────────┐
                    │  WORKER (sous-processus jetable, fait le BLE)│
   Node ──BLE──────▶│  • Client Proxy /e/ opaque -> broker MQTT    │──MQTT──▶ Mosquitto ─▶ MeshForge
                    │  • sonde métriques (getMyNodeInfo)           │
                    │  • exécute les commandes downlink            │
                    │  • os._exit sur décrochage (pas de teardown) │
                    └─────────────────────────────────────────────┘
```

Le worker fait tout le BLE et peut être **SIGKILL** ; le superviseur ne touche jamais au BLE donc
**ne gèle jamais**. Voir [resilience.md](resilience.md).

## Modules (`src/mbg/`)

| Module | Rôle |
|---|---|
| `config.py` | `Config` (dataclass) + `from_env()` — toute la config `MBG_*` |
| `proxy.py` | republie le `/e/` opaque au broker (ne crashe jamais) |
| `mqtt_publisher.py` | adaptateur paho MQTT |
| `node.py` | connexion BLE meshtastic + pubsub (proxy/lost/receive) + sonde de vivacité + ACK |
| `session.py` | une session BLE↔broker (boucle poll + heartbeat + monitor) |
| `worker.py` | corps du sous-processus jetable (`os._exit`) |
| `process_backend.py` | fork réel (`multiprocessing`) + handle worker |
| `supervisor.py` | pilote le worker : respawn/SIGKILL/disconnect, watchdog, paliers, API |
| `control.py` | traduit une commande (text/telemetry/position/request/admin) en appel meshtastic |
| `api.py` | API HTTP de contrôle (handler pur + serveur) |
| `storage.py` | sonde SQLite (WAL) : métriques + qualité lien + export CSV |
| `metrics.py` | lecteurs purs des métriques node/voisins |
| `tiers.py` | paliers batterie (constantes + hystérésis, pur) |
| `link_tuner.py` | supervision timeout BLE via `hcitool lecup` (opt-in) |
| `systemd_notify.py` | `sd_notify` (watchdog, sans dépendance) |

## Flux

- **Uplink (receive-only)** : node → BLE → `node.py` (pubsub `mqttclientproxymessage`) → `proxy.py`
  → broker. Payload `/e/` **opaque** (aucun déchiffrement côté passerelle). Log `[uplink] …`.
- **Downlink (opt-in)** : API → `supervisor.submit` → queue → worker → `node.send()` →
  `control.execute_command` → BLE. Log `[downlink] …`. Seul point qui rompt le « receive-only ».
