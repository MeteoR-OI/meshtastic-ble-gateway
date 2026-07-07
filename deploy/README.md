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

> **Archi** : le service lance un **superviseur** qui fait tourner le BLE dans un
> **worker (sous-processus) jetable**. Superviseur figé impossible (aucun BLE) → il nourrit
> `WatchdogSec` en continu ; systemd ne relance que si le superviseur meurt. Voir la section
> Résilience du README racine.
