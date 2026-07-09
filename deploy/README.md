# Déploiement V0.1 sur le RPi

Passerelle = forward opaque du Client Proxy `/e/` du node vers le broker MeshForge.
Le déchiffrement se fait côté MeshForge (voir plus bas).

## 1. Installer sur le RPi

```bash
sudo apt-get install -y git python3-venv
sudo git clone https://github.com/MeteoR-OI/meshtastic-ble-gateway.git /opt/meshtastic-ble-gateway
sudo useradd --system --home /opt/meshtastic-ble-gateway --shell /usr/sbin/nologin mbg
sudo usermod -aG bluetooth mbg          # accès à hci0
cd /opt/meshtastic-ble-gateway
sudo python3 -m venv .venv
sudo .venv/bin/pip install -e .         # ÉDITABLE : la maj = `git pull` + restart (cf. §4)
sudo chown -R mbg:mbg /opt/meshtastic-ble-gateway
```

> Install **éditable** (`-e`) volontaire : le code chargé pointe sur l'arbre git, donc un
> `git pull` suffit à mettre à jour (pas besoin de réinstaller). En non-éditable, `git pull`
> seul ne recharge PAS le code — piège rencontré en déploiement.

### Vérifier (read-only) que le node est bien en Client Proxy

`meshtastic --info` (v2.7.x) n'affiche PAS le `moduleConfig` → utiliser `--export-config` :

```bash
sudo timeout 60 .venv/bin/meshtastic --ble <MAC> --export-config | grep -iE "proxyToClient|jsonEnabled"
```

Attendu : `proxyToClientEnabled: true`, `jsonEnabled: true`. Ne rien modifier sur le node.
(À -89 dBm, un `Software caused connection abort` transitoire est possible : réessayer.)

## 2. Service systemd

```bash
sudo cp deploy/mbg.service /etc/systemd/system/
sudo nano /etc/systemd/system/mbg.service   # ajuster MBG_BLE_ADDRESS / MBG_BROKER_HOST
sudo systemctl daemon-reload
sudo systemctl enable --now mbg
journalctl -u mbg -f                          # suivre les [uplink]
```

> Sur RPi/BlueZ la cible BLE est la **MAC** (`E6:E3:53:4B:BE:A5`). Sur macOS c'est
> un UUID / le **nom** (`PAM_bea5`) — le PoC accepte les deux via `--ble`.

## 3. Configuration côté MeshForge (une fois)

Le node émet du `/e/` chiffré. Pour que MeshForge décode et affiche la couverture
**depuis le node** :

- `MESHTASTIC_CHANNEL_KEYS=Fr_Balise:AQ==,Fr_EMCOM:AQ==,Fr_BlaBla:AQ==` (clé par défaut).
- Ajouter ces noms de canaux à l'allowlist `public_channels` (admin `/admin/config`).

## 4. Mise à jour

Grâce à l'install éditable :

```bash
cd /opt/meshtastic-ble-gateway
sudo git pull
sudo systemctl restart mbg
journalctl -u mbg -f
```

(Réinstaller — `sudo .venv/bin/pip install -e .` — n'est nécessaire que si les
**dépendances** changent dans `pyproject.toml`.)

## Variables d'environnement (mbg)

| Var | Défaut | Rôle |
|-----|--------|------|
| `MBG_BLE_ADDRESS` | `E6:E3:53:4B:BE:A5` | MAC/nom BLE du node |
| `MBG_BROKER_HOST` | `localhost` | hôte du broker MQTT |
| `MBG_BROKER_PORT` | `1883` | port MQTT |
| `MBG_BROKER_USERNAME` / `MBG_BROKER_PASSWORD` | – | auth broker (optionnel) |
| `MBG_RECONNECT_DELAY` | `5` | délai initial du backoff de respawn du worker (s) |
| `MBG_MAX_RECONNECT_DELAY` | `30` | plafond du backoff exponentiel (s) |
| `MBG_POLL_INTERVAL` | `0.5` | cadence sonde/heartbeat du worker (s) |
| `MBG_SUPERVISOR_TICK` | `1` | cadence de surveillance du superviseur (s) |
| `MBG_CONNECT_GRACE` | `45` | délai toléré sans heartbeat pendant la connexion BLE (s) |
| `MBG_ALIVE_TIMEOUT` | `15` | gap max entre heartbeats une fois le worker connecté (s) |
| `MBG_API_TOKEN` | – | **token** de l'API de contrôle. Vide = **API désactivée** (défaut) |
| `MBG_API_HOST` | `0.0.0.0` | interface d'écoute de l'API (ex. `127.0.0.1` pour localhost) |
| `MBG_API_PORT` | `8080` | port de l'API |
| `MBG_CONTROL_TIMEOUT` | `10` | attente max d'une réponse worker à une commande (s) |
| `MBG_DB_PATH` | `metrics.db` | base SQLite des métriques (relative au WorkingDirectory) |
| `MBG_MONITOR_INTERVAL` | `300` | cadence de relevé des métriques node (s ; `0` = monitoring off) |
| `MBG_MONITOR_FORCE_TELEMETRY` | – | `true` = `sendTelemetry` avant chaque relevé (mesure fraîche, coûte de l'airtime) |
| `MBG_DUMP_DIR` | – | répertoire d'export CSV (vide = pas d'export) |
| `MBG_DUMP_INTERVAL` | `3600` | cadence export CSV + purge (s) |
| `MBG_RETENTION_DAYS` | `0` | purge des données au-delà de N jours (`0` = pas de purge) |
| `MBG_BATTERY_TIERS` | – | `true` = cadence adaptative + duty-cycle selon la batterie (nécessite le monitoring) |
| `MBG_DUTY_ON` | `300` | palier < 25 % : durée de la fenêtre de connexion (s) |
| `MBG_DUTY_OFF` | `1800` | palier < 25 % : durée de déconnexion entre fenêtres (s) |
| `MBG_TIER_HYSTERESIS` | `3` | marge (%) anti-flapping entre paliers |

## API de contrôle (downlink) — optionnelle

Activée uniquement si `MBG_API_TOKEN` est défini. Auth par en-tête `X-API-Token`.
Sécurité v1 : token + bind LAN/VPN (durcissement IP/localhost à venir). ⚠️ Ouvre un
chemin d'écriture BLE ; un write qui gèle est absorbé par l'isolation (worker SIGKILL).

```bash
TOKEN=... ; BASE=http://<rpi>:8080
# message texte sur un canal (nom ou index) :
curl -H "X-API-Token: $TOKEN" -d '{"text":"alerte","channel":"Fr_Balise"}' $BASE/send/text
# avec accusé d'émission radio (want_ack) -> log ASYNCHRONE "[downlink] ACK ... → reçu/échec" :
curl -H "X-API-Token: $TOKEN" -d '{"text":"test","channel":"Fr_Balise","want_ack":true}' $BASE/send/text
# télémétrie :
curl -H "X-API-Token: $TOKEN" -d '{}' $BASE/send/telemetry
# forcer une diffusion de position (rafraîchit la carte sans attendre le cycle 12 h) :
curl -H "X-API-Token: $TOKEN" -X POST $BASE/send/position          # ré-émet la position FIXE du node
curl -H "X-API-Token: $TOKEN" -d '{"lat":-21.34,"lon":55.47,"alt":120}' $BASE/send/position  # override explicite
# admin (rôle, intervalles…) :
curl -H "X-API-Token: $TOKEN" -d '{"setting":"position_broadcast_secs","value":43200}' $BASE/admin
curl -H "X-API-Token: $TOKEN" $BASE/health
```

Réponses : `200` ok, `401` token invalide, `503` aucun worker connecté, `504` timeout
worker, `400` commande invalide. Réglages admin : `role`, `position_broadcast_secs`,
`gps_mode`, `device_update_interval` (extensible dans `src/mbg/control.py`).

`/send/position` **fournit toujours des coordonnées** : sans payload, il **relit la
position fixe du node** et la ré-émet. C'est volontaire — `sendPosition()` sans coords
émettrait `0,0`, que le firmware **adopterait comme position locale** (il écraserait la
position fixe). Si le node n'a aucune position connue → `400` (refus d'émettre 0,0).

`want_ack` (optionnel, `/send/text`) demande un **accusé d'émission radio** : la réponse
HTTP reste immédiate (`ok`), et l'ACK/NAK arrive **plus tard** dans le journal
(`[downlink] ACK canal=… → reçu (ACK)` / `échec (…)`). Pour un broadcast, l'ACK est
implicite (un voisin rebroadcaste le paquet). Toutes les commandes sont tracées en INFO :
`[downlink] …` (audit, process superviseur) et `[downlink] ACK …` (asynchrone, worker).

> **Archi** : le service lance un **superviseur** qui fait tourner le BLE dans un
> **worker (sous-processus) jetable**. Superviseur figé impossible (aucun BLE) → il nourrit
> `WatchdogSec` en continu ; systemd ne relance que si le superviseur meurt. Voir la section
> Résilience du README racine.

## Monitoring / sonde (métriques)

Activé si `MBG_MONITOR_INTERVAL > 0`. Le worker relève **la batterie fraîche** (lecture
active `getMyNodeInfo`, contourne le broadcast 12 h), le voltage, l'utilisation canal/air,
la position et les **voisins directs + SNR/RSSI radio** ; le superviseur enregistre la
**qualité du lien BLE** via le **compteur de reconnexions** (`link_quality`). Stockage
**SQLite** (`MBG_DB_PATH`) — lisible directement par les scripts locaux, exposé par l'API
et exporté en **CSV** (`MBG_DUMP_DIR`).

> Pas de RSSI absolu du lien BLE : sur BlueZ, `bluetoothd` détient le contrôleur, donc le
> RSSI d'un lien LE connecté n'est plus exposé (ni `hcitool rssi`, ni `btmgmt conn-info`,
> ni D-Bus `Device1.RSSI`, même en root) — vérifié terrain. Le **compteur de reconnexions**
> est le signal de qualité BLE.

```bash
curl -H "X-API-Token: $TOKEN" $BASE/metrics                 # dernier relevé {node, link}
curl -H "X-API-Token: $TOKEN" "$BASE/history?since=0&limit=100"   # série node_metrics
```

Créer les répertoires avec les bons droits pour l'utilisateur `mbg` :
```bash
sudo install -d -o mbg -g mbg /var/lib/mbg   # ex. MBG_DB_PATH=/var/lib/mbg/metrics.db, MBG_DUMP_DIR=/var/lib/mbg/csv
```

## Paliers batterie + duty-cycle (V0.4) — optionnel

Activé si `MBG_BATTERY_TIERS=true` (**nécessite le monitoring** comme source de batterie ;
sinon un WARNING est loggé et l'option est ignorée). Le superviseur lit la dernière batterie
du node et **adapte le comportement** pour préserver **la batterie du node** (un lien BLE
permanent empêche son light-sleep) :

| Batterie | Cadence de relevé | Lien BLE |
|---|---|---|
| ≥ 75 % | 15 min | live (proxy) |
| ≥ 50 % | 30 min | live |
| ≥ 25 % | 60 min | live |
| < 25 % | 1 relevé / fenêtre | **duty-cycle** : ON `MBG_DUTY_ON` (5 min) / OFF `MBG_DUTY_OFF` (30 min) |

- **Duty-cycle (< 25 %)** : le lien est **coupé** pendant le OFF (le node dort) → **uplinks
  perdus** sur la fenêtre (trous de flux **assumés**). Le OFF est **watchdog-friendly** (il
  dépasse `WatchdogSec` : le superviseur continue de nourrir systemd, pas de faux redémarrage).
  Pendant le OFF, `GET /metrics` reste servi mais l'API de contrôle renvoie `503`.
- **Hystérésis** (`MBG_TIER_HYSTERESIS`, 3 %) : descente au seuil nominal, remontée seulement
  au-delà de seuil + hystérésis → pas de flapping (surtout autour du seuil critique 25 %).
- **Télémétrie au changement de mode** : à chaque transition de palier, la session suivante
  force un `sendTelemetry` (broadcast) → la batterie fraîche est **diffusée sur le mesh**.
- Contrainte : garder `MBG_MAX_RECONNECT_DELAY` < `WatchdogSec` (défaut 30 < 120).
