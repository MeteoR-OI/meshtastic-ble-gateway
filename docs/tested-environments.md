# Environnements testés

La passerelle est **déployée en production** sur des Raspberry Pi en Raspberry Pi OS **Bullseye**
et **Buster**, et testée en CI + conteneurs Docker.

## Systèmes hôtes

| OS | Python | BlueZ | Notes |
|---|---|---|---|
| Raspberry Pi OS 11 (**Bullseye**) | 3.9 (système) | 5.55 ✅ | cas nominal, rien à isoler |
| Raspberry Pi OS 10 (**Buster**) | 3.7 système → **3.11 isolé** (`/opt`) | 5.50 ❌ → **5.55** (`bluez-meshforge`) | voir [installation](installation.md#cas-raspbian-buster) |

- **BlueZ** : **≥ 5.55 requis** — sous 5.50 la connexion s'établit mais **aucune notification GATT**
  ne remonte (aucune donnée). Vérifié terrain.
- **Buster** : le `python3` système (3.7) et BlueZ (5.50) sont **laissés intacts** ; on installe
  Python 3.11 + BlueZ 5.55 **à côté** (`/opt`).

## Python (CI + tooling)

| Version | Usage |
|---|---|
| 3.9 | plancher (`requires-python>=3.9`), cible Bullseye — vérifié en conteneur `python:3.9` et `python:3.9-buster` |
| 3.11 | Python isolé recommandé sur Buster |
| 3.11 / 3.13 | CI + développement |

100 % branch coverage sur toutes ces versions.

## Dépendances clés

| Paquet | Version | Remarque |
|---|---|---|
| `meshtastic` | 2.7.10 | exige Python ≥ 3.9 |
| `bleak` | **1.1.1** (pin) | bleak 3.x **casse sur BlueZ < 5.52** (`KeyError: 'Roles'`) → pin via `constraints.txt` |
| `paho-mqtt` | 2.1.x | broker MQTT |
| `dbus-fast`, `protobuf`, `pyserial` | — | tirés par meshtastic ; wheelhouse armhf fourni pour Buster |

## Matériel node validé

Nodes Meshtastic **Bluetooth-only** en firmware **Client Proxy** (émettent uniquement du `/e/`
chiffré) — validés par type : **Heltec T114** et **RAK4631 / nRF52840** (rôle CLIENT).

> La passerelle est **agnostique du node** : elle forwarde le `/e/` opaque, sans clé ni
> déchiffrement (tout le crypto vit côté MeshForge).
