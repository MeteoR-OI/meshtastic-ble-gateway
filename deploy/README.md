# Déploiement V0.1 sur le RPi (MHA235)

Passerelle = forward opaque du Client Proxy `/e/` du node vers le broker MeshForge.
Le déchiffrement se fait côté MeshForge (voir plus bas).

## 1. Installer sur le RPi

```bash
sudo mkdir -p /opt/meshtastic-ble-gateway
sudo useradd --system --home /opt/meshtastic-ble-gateway --shell /usr/sbin/nologin mbg
sudo usermod -aG bluetooth mbg          # accès à hci0
# copier le repo dans /opt/meshtastic-ble-gateway, puis :
cd /opt/meshtastic-ble-gateway
sudo python3 -m venv .venv
sudo .venv/bin/pip install .
sudo chown -R mbg:mbg /opt/meshtastic-ble-gateway
```

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

## Variables d'environnement (mbg)

| Var | Défaut | Rôle |
|-----|--------|------|
| `MBG_BLE_ADDRESS` | `E6:E3:53:4B:BE:A5` | MAC/nom BLE du node |
| `MBG_BROKER_HOST` | `localhost` | hôte du broker MQTT |
| `MBG_BROKER_PORT` | `1883` | port MQTT |
| `MBG_BROKER_USERNAME` / `MBG_BROKER_PASSWORD` | – | auth broker (optionnel) |
| `MBG_RECONNECT_DELAY` | `5` | délai (s) entre tentatives de session |
| `MBG_POLL_INTERVAL` | `0.5` | granularité de la boucle |
