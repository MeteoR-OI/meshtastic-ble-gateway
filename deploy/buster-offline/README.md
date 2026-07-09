# Install hors-ligne sur Raspbian Buster (armhf) — artefacts pré-compilés

Sur un Pi Buster armv7, compiler Python 3.11 + les wheels natives prend ~40 min. On construit
**une fois** deux artefacts et le Pi installe **hors-ligne, en minutes, sans compilateur** :

- `python311-meshforge_3.11.9-1~buster_armhf.deb` — Python 3.11 sous `/opt/python3.11` (n'écrase
  pas le python système 3.7).
- `wheelhouse-buster-py311.tar.gz` — toutes les dépendances (`meshtastic` + `bleak==1.1.1` +
  `dbus-fast`, `protobuf`, `pyserial`…) en **wheels armhf/cp311**.

> À combiner avec le paquet **`bluez-meshforge`** (BlueZ 5.55, cf. `../bluez-buster/`).

## Construire les artefacts (une fois)

**Sur une machine ARM Buster réelle** (un Pi armv7, ou le même environnement qui a servi à
builder `bluez-meshforge`) → build **natif, sans qemu**. Dans un conteneur
`arm32v7/debian:buster` (comme pour le BlueZ) ; depuis la racine du repo :

```bash
mkdir -p deploy/buster-offline/out
docker run --rm \
  -v "$PWD:/src" -v "$PWD/deploy/buster-offline/out:/out" \
  arm32v7/debian:buster-slim bash /src/deploy/buster-offline/build-buster-offline.sh
# -> out/python311-meshforge_3.11.9-1~buster_armhf.deb
# -> out/wheelhouse-buster-py311.tar.gz
```

Sur un Pi armv7, ~40 min (CPython + wheels natives), **une seule fois**. Les artefacts sont
ensuite **attachés à la release GitHub** (on ne versionne pas de binaires), et tous les autres
Pi Buster installent hors-ligne en minutes.

> Repli uniquement si on doit builder sur un hôte **x86/arm64** (pas d'ARM sous la main) :
> ajouter `--platform linux/arm/v7` à `docker run` → build via **qemu émulé**, beaucoup plus
> lent (~1–2 h). À éviter si un vrai ARM est disponible.

## Installer sur le Pi Buster (hors-ligne)

```bash
# 1) Python 3.11 isolé + BlueZ 5.55  (glob : GitHub renomme ~ -> . dans les assets)
sudo dpkg -i python311-meshforge_*.deb
sudo dpkg -i bluez-meshforge_*.deb        # cf. ../bluez-buster/
python3 --version                          # <- toujours 3.7 (système intact)

# 2) passerelle : venv depuis le python isolé + install depuis le wheelhouse (zéro compilation)
cd /opt/meshtastic-ble-gateway
tar xzf /chemin/wheelhouse-buster-py311.tar.gz
sudo /opt/python3.11/bin/python3.11 -m venv .venv
sudo .venv/bin/pip install --no-index --find-links wheelhouse -e . -c constraints.txt
```

`--no-index` garantit qu'**aucune** dépendance n'est téléchargée/compilée : tout vient du
wheelhouse. Poursuivre ensuite au [service systemd](../../docs/installation.md#3-service-systemd).

## Notes

- Le wheelhouse est lié à la version exacte de Python (**cp311**) : rebuild si on change de
  version Python.
- Combo pinné (`constraints.txt`) : `bleak==1.1.1` (compat BlueZ Buster). Voir `../bluez-buster/`.
