# BlueZ 5.55 pour Raspbian 10 (Buster)

## Pourquoi

Raspbian **Buster** livre **BlueZ 5.50**. `bleak`/`meshtastic` exigent **BlueZ ≥ 5.55**
pour les **notifications GATT** : sous 5.50 la connexion BLE s'établit **mais aucune donnée
ne remonte** (le worker se connecte puis ne reçoit jamais de paquet). Un hôte Bullseye
(BlueZ 5.55) n'a pas ce problème ; un hôte Buster, si.

Ce paquet **`bluez-meshforge`** installe **BlueZ 5.55 sous `/opt`** (`bluetoothd` + `bluetoothctl`)
**sans toucher au BlueZ système** : il pose seulement un drop-in systemd
`bluetooth.service.d/override.conf` qui bascule `bluetoothd` vers celui de 5.55. La config
(`/etc/bluetooth/main.conf`) et les appairages (`/var/lib/bluetooth`) du système sont réutilisés
tels quels (build avec `--localstatedir=/var --sysconfdir=/etc`).

> À installer **avant** de lancer `mbg` sur Buster. Sur **Bullseye et +** (BlueZ ≥ 5.55) :
> **inutile**.

## Construire le .deb (dans un conteneur Buster armhf)

Sur une machine avec Docker + qemu (ou sur un Pi armv7 Buster) :

```bash
cd deploy/bluez-buster && mkdir -p out
docker run --rm --platform linux/arm/v7 \
  -v "$PWD/build-bluez-deb.sh:/b.sh" -v "$PWD/out:/out" \
  arm32v7/debian:buster-slim bash /b.sh
# -> out/bluez-meshforge_5.55-1~buster_armhf.deb
```

Le `.deb` peut aussi être récupéré depuis la **release GitHub** correspondante (évite de
versionner un binaire dans le repo).

## Installer sur le Pi Buster

```bash
sudo dpkg -i bluez-meshforge_*.deb   # glob : GitHub renomme ~ -> . ; apt-get install -f si deps manquantes
bluetoothctl --version               # doit afficher 5.55
```

`bluetoothctl` 5.55 est dans `/opt/bluez-5.55/bin` → le service `mbg` doit l'avoir dans son
`PATH` (bleak lit `bluetoothctl --version` du PATH). Voir
[docs/installation.md](../../docs/installation.md#cas-raspbian-buster) (variable `PATH` du service).

## Rollback

```bash
sudo dpkg -r bluez-meshforge   # revient au bluetoothd 5.50 système (postrm relance bluetooth)
```
