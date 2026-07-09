# deploy/

Fichiers de déploiement de la passerelle sur Raspberry Pi.

- **[`mbg.service`](mbg.service)** — unité systemd de référence (variables d'environnement
  commentées prêtes à l'emploi).
- **[`bluez-buster/`](bluez-buster/README.md)** — paquet `bluez-meshforge` (BlueZ 5.55 sous `/opt`)
  pour Raspberry Pi OS **Buster** (recette de build reproductible + rollback).
- **[`buster-offline/`](buster-offline/README.md)** — artefacts pré-compilés (Python 3.11 `.deb` +
  wheelhouse armhf) pour une **installation hors-ligne sans compilation** sur Buster.

## Où trouver quoi

| Sujet | Documentation |
|---|---|
| Guide d'installation complet (Bullseye & Buster, systemd, MeshForge) | **[docs/installation.md](../docs/installation.md)** |
| Variables d'environnement `MBG_*` | [docs/configuration.md](../docs/configuration.md) |
| API de contrôle | [docs/api.md](../docs/api.md) |
| Monitoring / sonde | [docs/monitoring.md](../docs/monitoring.md) |
| Paliers batterie + duty-cycle | [docs/battery-tiers.md](../docs/battery-tiers.md) |
| Résilience & tuning du lien BLE | [docs/resilience.md](../docs/resilience.md) |
| Environnements testés | [docs/tested-environments.md](../docs/tested-environments.md) |
| Dépannage & caveats | [docs/troubleshooting.md](../docs/troubleshooting.md) |
