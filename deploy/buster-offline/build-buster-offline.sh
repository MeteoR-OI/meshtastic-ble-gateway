#!/bin/bash
# Construit, pour Raspbian 10 (Buster / armhf), des artefacts d'installation SANS compilation
# sur le Pi (install en minutes au lieu de ~40 min) :
#   - python311-meshforge_<PYV>-1~buster_armhf.deb   -> Python 3.11 sous /opt/python3.11
#   - wheelhouse-buster-py311.tar.gz                 -> toutes les deps en wheels armhf/cp311
#
# À exécuter DANS un conteneur arm32v7/debian:buster (voir README.md). Volumes attendus :
#   /src = racine du repo (pour constraints.txt + pyproject)   /out = répertoire de sortie
set -euxo pipefail
PYV=3.11.9

cat > /etc/apt/sources.list <<'EOF'
deb http://archive.debian.org/debian buster main
deb http://archive.debian.org/debian buster-updates main
deb http://archive.debian.org/debian-security buster/updates main
EOF
printf 'Acquire::Check-Valid-Until "false";\n' > /etc/apt/apt.conf.d/99no-check-valid
export DEBIAN_FRONTEND=noninteractive
apt-get update
# libs de build (Python + wheels natives : dbus-fast, protobuf…)
apt-get install -y --no-install-recommends build-essential wget ca-certificates xz-utils pkg-config \
  libssl-dev zlib1g-dev libbz2-dev libreadline-dev libsqlite3-dev libffi-dev libncurses5-dev \
  liblzma-dev uuid-dev libdbus-1-dev libglib2.0-dev

# --- 1) Python 3.11 -> paquet .deb (/opt/python3.11, n'écrase pas le python système 3.7) ---
cd /usr/src
wget -q https://www.python.org/ftp/python/$PYV/Python-$PYV.tgz
tar xzf Python-$PYV.tgz && cd Python-$PYV
./configure --prefix=/opt/python3.11          # (sans --enable-optimizations : build émulé + rapide)
make -j"$(nproc)"
PKG=/tmp/pyroot; rm -rf "$PKG"
make altinstall DESTDIR="$PKG"                 # altinstall : binaire python3.11 (pas python3)
mkdir -p "$PKG/DEBIAN"
cat > "$PKG/DEBIAN/control" <<EOF
Package: python311-meshforge
Version: $PYV-1~buster
Architecture: armhf
Maintainer: MeteoR-OI <infra@meteor-oi.re>
Depends: libc6, libssl1.1, zlib1g, libbz2-1.0, libreadline7, libsqlite3-0, libffi6, libncursesw6, liblzma5, libuuid1
Section: python
Priority: optional
Description: Python $PYV isolé sous /opt pour Raspbian Buster (meshtastic-ble-gateway)
 Interpréteur 3.11 requis par meshtastic (>= 3.9). N'écrase PAS le python système (3.7).
EOF
dpkg-deb --build --root-owner-group "$PKG" /out/python311-meshforge_${PYV}-1~buster_armhf.deb
cp -a "$PKG/opt/python3.11" /opt/python3.11    # l'installe ici pour construire le wheelhouse avec CE python

# --- 2) Wheelhouse : toutes les deps (pinnées) en wheels armhf/cp311 ---
/opt/python3.11/bin/python3.11 -m venv /tmp/wh
/tmp/wh/bin/pip install -U pip wheel
/tmp/wh/bin/pip wheel -w /out/wheelhouse -c /src/constraints.txt /src
cd /out && tar czf wheelhouse-buster-py311.tar.gz wheelhouse && rm -rf wheelhouse

echo "=== Artefacts produits dans /out ==="; ls -l /out
