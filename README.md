<div align="center">

# 🌉 meshtastic-ble-gateway

**Pont BLE → MQTT pour nodes Meshtastic BT-only, à destination de [MeshForge](https://github.com/Robin-Lune/meshforge).**

</div>

---

## Pourquoi ?

MeshForge n'ingère que du **MQTT**. Un node Meshtastic **Bluetooth-only** (ex. Heltec
T114) ne peut pas publier tout seul (pas de WiFi). Ce pont, hébergé sur un Raspberry
Pi, se connecte au node en **BLE** et relaie son trafic vers le broker Mosquitto que
MeshForge consomme.

```
[Node BT-only] ──BLE──▶ [RPi : meshtastic-ble-gateway] ──MQTT──▶ Mosquitto ──▶ MeshForge
```

## Roadmap

| Version | Contenu | État |
|---------|---------|------|
| **PoC** | Client Proxy over BLE, validé sur T114 réel (n'émet que du `/e/`) | ✅ `poc/` |
| **V0.1** | Passerelle durcie (`src/mbg/`, tests 100 %, CI, Docker, systemd) — déploiement RPi | ✅ |
| **V0.2** | Monitoring : stockage local SQLite des infos node (base de la « sonde ») | à venir |
| **V0.3** | Paliers batterie + duty-cycle du lien BLE | à venir |

## Lancer la passerelle (V0.1)

```bash
python -m venv .venv && ./.venv/bin/pip install -e ".[dev]"
./.venv/bin/python -m mbg --ble PAM_bea5 --broker localhost   # macOS : nom ; RPi : MAC
```

Config aussi possible par variables d'env (`MBG_*`, cf. `deploy/README.md`). La
passerelle forwarde le `/e/` **opaque** ; MeshForge déchiffre via
`MESHTASTIC_CHANNEL_KEYS`. Déploiement RPi (systemd) : voir `deploy/README.md`.

### Résilience (BLE instable)

Trois niveaux, du plus fin au plus grossier :

1. **Perte de lien détectée** : abonnement à `meshtastic.connection.lost` → la session se
   termine et se relance après `MBG_RECONNECT_DELAY` (backoff).
2. **Échec de (re)connexion** node/broker : même boucle de reconnexion.
3. **Crash complet du process** : `systemd Restart=always` relance le service.

> Limite connue : un lien qui se fige *sans* que meshtastic n'émette `connection.lost`
> ne serait pas encore détecté (un watchdog « pas de trafic depuis N min » est envisagé
> pour une itération ultérieure — écarté ici pour éviter les faux positifs sur mesh calme).

### Tests

```bash
pytest                              # unitaires, 100 % branch coverage
docker compose -f poc/docker-compose.yml up -d && pytest tests/integration --no-cov
```

### Paliers batterie (V0.3)

| Batterie | Monitoring | Lien BLE / proxy |
|----------|-----------|------------------|
| > 75 % | poll 15 min | connecté, proxy live |
| > 50 % | poll 30 min | connecté, proxy live |
| > 25 % | poll 60 min | connecté, proxy live |
| < 25 % | 1 poll/fenêtre | duty-cycle 5 min / 30 min (⚠️ trous de flux assumés) |

## Deux mécanismes possibles

1. **Client Proxy firmware** (testé par le PoC) : le node produit ses trames MQTT, on
   les relaie **telles quelles**. Minimal si ça marche sur BLE.
2. **Nodeless republish** (repli) : on reçoit les paquets décodés (`meshtastic.receive`)
   et on **reconstruit** le JSON attendu par MeshForge (`msh/<region>/<gwnum>/json/<channel>/<gwid>`).
   Plus de code, mais robuste sur BLE. Retenu si le PoC échoue.

## Démarrer

Voir **[`poc/README.md`](poc/README.md)** pour valider le Client Proxy over BLE sur ton node.

## Licence

AGPL-3.0-or-later — cohérent avec l'écosystème MeshForge.
