# Installation & déploiement

Déploiement de la passerelle sur un Raspberry Pi (service systemd). La passerelle relaie le
Client Proxy `/e/` **opaque** du node vers le broker MQTT ; le déchiffrement se fait côté
MeshForge ([§4](#4-configuration-côté-meshforge)).

## Sommaire

- [Prérequis Python (Bullseye vs Buster)](#prérequis-python-bullseye-vs-buster)
- [Cas Raspbian Buster (Python + BlueZ isolés)](#cas-raspbian-buster)
- [1. Installer la passerelle](#1-installer-la-passerelle)
- [2. Vérifier le node (read-only)](#2-vérifier-le-node-read-only)
- [3. Service systemd](#3-service-systemd)
  - [Gestion du service au quotidien](#gestion-du-service-au-quotidien)
- [4. Configuration côté MeshForge](#4-configuration-côté-meshforge)
- [5. Mise à jour](#5-mise-à-jour)

Environnements validés : voir [tested-environments.md](tested-environments.md).

---

## Prérequis Python (Bullseye vs Buster)

`meshtastic` (2.7.x) exige **Python ≥ 3.9**.

- **Raspberry Pi OS 11 (Bullseye)** et + : `python3` système déjà en 3.9 → passer directement au
  [§1](#1-installer-la-passerelle).
- **Raspberry Pi OS 10 (Buster)** : `python3` système en **3.7** (trop vieux) **et** BlueZ **5.50**
  (trop vieux pour les notifications GATT). → voir [Cas Buster](#cas-raspbian-buster) d'abord.

## Cas Raspbian Buster

Sur Buster il faut un **Python 3.9+ isolé** et **BlueZ ≥ 5.55**, **sans jamais toucher au système**
(dont dépendent `apt`, `raspi-config`…).

> 💡 **Raccourci sans compilation (~40 min évités)** : installer les **artefacts pré-compilés**
> (Python 3.11 en `.deb` + wheelhouse armhf, joints à la [release](https://github.com/MeteoR-OI/meshtastic-ble-gateway/releases)).
> Voir **[`deploy/buster-offline/`](../deploy/buster-offline/README.md)**. Sinon, méthode « sources » ci-dessous.

### a) Python 3.11 isolé (compilé en `altinstall` sous `/opt`)

> ⚠️ **Zéro impact sur le Python système.** **Jamais** `apt install python3.x`, **jamais**
> `make install` (uniquement `make altinstall`), **jamais** `update-alternatives` sur `python3`.
> Les paquets `apt` ci-dessous sont des **libs de build** (`-dev`), pas des paquets Python.

```bash
sudo apt-get update
sudo apt-get install -y build-essential wget libssl-dev zlib1g-dev libbz2-dev \
  libreadline-dev libsqlite3-dev libffi-dev libncurses5-dev liblzma-dev uuid-dev

cd /usr/src
PYV=3.11.9
sudo wget -q https://www.python.org/ftp/python/$PYV/Python-$PYV.tgz
sudo tar xzf Python-$PYV.tgz && cd Python-$PYV
sudo ./configure --prefix=/opt/python3.11 --enable-optimizations
sudo make -j"$(nproc)"            # ~20-40 min (armv7)
sudo make altinstall             # n'écrase pas /usr/bin/python3

# vérifier l'isolation :
/opt/python3.11/bin/python3.11 --version   # Python 3.11.9
python3 --version                          # <- INCHANGÉ : le 3.7 système
```

Au [§1](#1-installer-la-passerelle), créer le venv **depuis ce Python isolé** :
`sudo /opt/python3.11/bin/python3.11 -m venv .venv`.

> Alternative : **pyenv** (`~/.pyenv`, isolé aussi). Éviter les conteneurs pour le BLE
> (accès `hci0`/D-Bus depuis Docker = fragile).

### b) BlueZ 5.55

Sous BlueZ 5.50, la connexion BLE s'établit **mais aucune donnée ne remonte** (notifications
GATT KO). Installer le paquet **`bluez-meshforge`** (BlueZ 5.55 sous `/opt`, sans toucher au
BlueZ système) — joint à la [release](https://github.com/MeteoR-OI/meshtastic-ble-gateway/releases),
ou construit via [`deploy/bluez-buster/`](../deploy/bluez-buster/README.md) :

```bash
sudo dpkg -i bluez-meshforge_*.deb     # glob : GitHub renomme ~ -> . dans les assets
bluetoothctl --version                 # -> 5.55
```

- **`PATH` du service** : `bleak` lit `bluetoothctl --version` du PATH → ajouter dans le service :
  `Environment=PATH=/opt/bluez-5.55/bin:/usr/local/bin:/usr/bin:/bin`.
- **Appairage `Paired` mais NON `Trusted`** : un node *trusted* est auto-reconnecté par
  `bluetoothd` → il cesse d'émettre → boucle `No peripheral found`. Retirer le trust (garder
  l'appairage) : `bluetoothctl untrust <MAC-du-node>`. Voir [troubleshooting](troubleshooting.md).

### c) Reproductibilité (pin `bleak`)

`bleak` (dépendance transitive) n'est pas pinné → **bleak 3.x casse sur BlueZ < 5.52**. Installer
avec le fichier de contraintes (`bleak==1.1.1`, combo prouvé) : ajouter **`-c constraints.txt`** à
la commande `pip install` du [§1](#1-installer-la-passerelle).

---

## 1. Installer la passerelle

```bash
sudo apt-get install -y git python3-venv
sudo git clone https://github.com/MeteoR-OI/meshtastic-ble-gateway.git /opt/meshtastic-ble-gateway
sudo useradd --system --home /opt/meshtastic-ble-gateway --shell /usr/sbin/nologin mbg
sudo usermod -aG bluetooth mbg          # accès à hci0
cd /opt/meshtastic-ble-gateway

sudo python3 -m venv .venv              # Buster : /opt/python3.11/bin/python3.11 -m venv .venv
sudo .venv/bin/pip install -e .         # Buster : ajouter  -c constraints.txt
sudo chown -R mbg:mbg /opt/meshtastic-ble-gateway
```

> Install **éditable** (`-e`) volontaire : la mise à jour = `git pull` + restart (le code chargé
> pointe sur l'arbre git). En non-éditable, `git pull` seul ne recharge pas le code.

**Buster hors-ligne** (artefacts pré-compilés) : voir
[`deploy/buster-offline/`](../deploy/buster-offline/README.md) (`pip install --no-index --find-links wheelhouse …`).

## 2. Vérifier le node (read-only)

Le node doit être en **Client Proxy**. `meshtastic --info` n'affiche pas le `moduleConfig` en
2.7.x → utiliser `--export-config` (ne **rien modifier** sur le node) :

```bash
sudo timeout 60 .venv/bin/meshtastic --ble <MAC-du-node> --export-config | grep -iE "proxyToClient|jsonEnabled"
```

Attendu : `proxyToClientEnabled: true`. (À faible signal, un `Software caused connection abort`
transitoire est possible : réessayer.)

## 3. Service systemd

```bash
sudo cp deploy/mbg.service /etc/systemd/system/
sudo nano /etc/systemd/system/mbg.service   # régler MBG_BLE_ADDRESS / MBG_BROKER_HOST + creds
sudo systemctl daemon-reload
sudo systemctl enable --now mbg
journalctl -u mbg -f                          # suivre les [uplink]
```

- Toutes les variables : [configuration.md](configuration.md). Cible BLE sur Linux/BlueZ = la
  **MAC** ; sur macOS = UUID/nom (le CLI accepte les deux via `--ble`).
- Créer le répertoire des métriques si le monitoring est activé :
  `sudo install -d -o mbg -g mbg /var/lib/mbg`.
- **Identité des logs** : l'unité pose `SyslogIdentifier=meteor-mbg`, donc le service journalise
  sous le tag `meteor-mbg` (et non le générique `python` de l'interpréteur). Il se retrouve
  ainsi sous `app_name:meteor-mbg` dans un agrégateur type VictoriaLogs, distinct des autres
  services Python de la station.

### Gestion du service au quotidien

Une fois installé, le service se pilote avec `systemctl` / `journalctl` :

```bash
# Cycle de vie
sudo systemctl stop mbg        # arrêter (libère le BLE — utile pour un meshtastic --ble manuel)
sudo systemctl start mbg       # démarrer
sudo systemctl restart mbg     # redémarrer (après un git pull, un changement d'ENV, etc.)

# État
systemctl is-active mbg        # statut court : active / inactive / failed
systemctl status mbg           # statut détaillé (PID, uptime, dernières lignes de log)

# Logs
sudo journalctl -u mbg -f                  # suivre en direct (Ctrl-C pour sortir)
sudo journalctl -u mbg -n 50 --no-pager    # les 50 dernières lignes

# Démarrage au boot
sudo systemctl enable mbg      # redémarrage auto au boot (état par défaut après §3)
sudo systemctl disable mbg     # ne plus démarrer au boot
```

> Le BLE n'accepte **qu'un seul client à la fois** : pour lancer un `meshtastic --ble …` à la
> main (diagnostic, `--export-config`), faire d'abord `sudo systemctl stop mbg`, puis
> `sudo systemctl start mbg` une fois terminé.

## 4. Configuration côté MeshForge

Le node émet du `/e/` chiffré ; MeshForge déchiffre. Une fois côté MeshForge :

- `MESHTASTIC_CHANNEL_KEYS=MonCanal:AQ==` (clé par défaut `AQ==` ; lister chaque canal uplink).
- Ajouter ces noms de canaux à l'allowlist `public_channels` (admin `/admin/config`).

## 5. Mise à jour

```bash
cd /opt/meshtastic-ble-gateway
sudo git pull && sudo systemctl restart mbg
journalctl -u mbg -f
```

Réinstaller (`sudo .venv/bin/pip install -e .`) n'est nécessaire que si les **dépendances**
changent dans `pyproject.toml`.
