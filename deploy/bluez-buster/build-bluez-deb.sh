#!/bin/bash
# Construit un .deb `bluez-meshforge` (BlueZ 5.55 sous /opt) pour Raspbian 10 (Buster).
# À exécuter DANS un conteneur arm32v7/debian:buster-slim (voir README.md) — ABI Buster
# garantie, zéro charge sur le Pi. Recette validée terrain (CHAR645).
set -euxo pipefail
BZ=5.55
cat > /etc/apt/sources.list <<'EOF'
deb http://archive.debian.org/debian buster main
deb http://archive.debian.org/debian buster-updates main
deb http://archive.debian.org/debian-security buster/updates main
EOF
printf 'Acquire::Check-Valid-Until "false";\n' > /etc/apt/apt.conf.d/99no-check-valid
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends build-essential wget ca-certificates xz-utils pkg-config \
  libglib2.0-dev libdbus-1-dev libudev-dev libreadline-dev
cd /tmp && wget -q https://mirrors.edge.kernel.org/pub/linux/bluetooth/bluez-$BZ.tar.xz
tar xf bluez-$BZ.tar.xz && cd bluez-$BZ
./configure --prefix=/opt/bluez-$BZ --localstatedir=/var --sysconfdir=/etc \
  --disable-systemd --disable-cups --disable-obex --disable-mesh --disable-manpages \
  --enable-client --enable-tools --enable-datafiles \
  --with-udevdir=/opt/bluez-$BZ/lib/udev --with-dbusconfdir=/opt/bluez-$BZ/etc
make -j"$(nproc)"
PKG=/tmp/pkgroot; rm -rf "$PKG"; mkdir -p "$PKG"; make install DESTDIR="$PKG"
rm -rf "$PKG/etc" "$PKG/var" "$PKG/lib"   # ne rien poser hors de /opt (pas de conffile en conflit)
mkdir -p "$PKG/etc/systemd/system/bluetooth.service.d"
cat > "$PKG/etc/systemd/system/bluetooth.service.d/override.conf" <<'EOF'
[Service]
ExecStart=
ExecStart=/opt/bluez-5.55/libexec/bluetooth/bluetoothd
EOF
mkdir -p "$PKG/DEBIAN"
cat > "$PKG/DEBIAN/control" <<EOF
Package: bluez-meshforge
Version: 5.55-1~buster
Architecture: armhf
Maintainer: MeteoR-OI <infra@meteor-oi.re>
Depends: bluez, libc6, libglib2.0-0, libdbus-1-3, libudev1, libreadline7, libpcre3, libsystemd0, liblzma5, liblz4-1, libgcrypt20, libgpg-error0
Section: comm
Priority: optional
Description: BlueZ 5.55 (bluetoothd + bluetoothctl) sous /opt pour Raspbian Buster
 Requis par meshtastic-ble-gateway sur Buster (BlueZ 5.50) : bleak/meshtastic
 exigent BlueZ >= 5.55 pour les notifications GATT.
EOF
printf '#!/bin/sh\nset -e\nif [ "$1" = configure ]; then systemctl daemon-reload||true; systemctl restart bluetooth||true; fi\n' > "$PKG/DEBIAN/postinst"
printf '#!/bin/sh\nset -e\nif [ "$1" = remove ] || [ "$1" = purge ]; then systemctl daemon-reload||true; systemctl restart bluetooth||true; fi\n' > "$PKG/DEBIAN/postrm"
chmod 0755 "$PKG/DEBIAN/postinst" "$PKG/DEBIAN/postrm"
dpkg-deb --build --root-owner-group "$PKG" /out/bluez-meshforge_5.55-1~buster_armhf.deb
