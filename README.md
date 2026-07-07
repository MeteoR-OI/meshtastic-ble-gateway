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

## Lancer & configurer (V0.1)

Deux sources de config : l'**environnement (`MBG_*`) fournit la base** — c'est ainsi que
systemd configure le service — et les **arguments CLI priment** s'ils sont fournis (usage
manuel / PoC).

```bash
python -m venv .venv && ./.venv/bin/pip install -e ".[dev]"

# manuel, via CLI (macOS : nom BLE ; RPi : MAC) :
./.venv/bin/python -m mbg --ble PAM_bea5 --broker localhost

# ou via l'environnement, comme le service systemd (aucun argument) :
MBG_BLE_ADDRESS=F9:... MBG_BROKER_HOST=mqtt.example ./.venv/bin/python -m mbg
```

Table complète des variables `MBG_*` et déploiement RPi (systemd) :
voir [`deploy/README.md`](deploy/README.md).

La passerelle forwarde le `/e/` **opaque** ; MeshForge déchiffre via
`MESHTASTIC_CHANNEL_KEYS` (le node n'émet que du `/e/` chiffré en Client Proxy, pas de
`/json/`).

### Résilience (BLE instable)

Le BLE décroche — souvent **silencieusement**. La passerelle traite ça comme la norme :

0. **Anti-gel** (`meshtastic_patch`) : sur lien mort, `meshtastic.close()` gelait en
   écrivant un paquet « disconnect » au radio (`write_gatt_char` sans timeout — cause
   confirmée par py-spy). Comme la passerelle ne fait que *recevoir*, on **neutralise ce
   `_sendDisconnect`** → la fermeture n'accroche plus, donc la reconnexion in-process
   ci-dessous **va au bout** (au lieu de dépendre d'un restart systemd).
1. **Coupure silencieuse détectée** : à chaque poll, une **sonde de vivacité** lit l'état
   BlueZ (`is_connected` via bleak) — le seul signal fiable quand meshtastic n'émet ni
   exception ni `connection.lost`. Lien mort → session fermée → reconnexion.
2. **Coupure signalée** : abonnement à `meshtastic.connection.lost` (redondant avec 1).
3. **Échec de (re)connexion** node/broker : même boucle, **backoff exponentiel plafonné**
   (`MBG_RECONNECT_DELAY` → `MBG_MAX_RECONNECT_DELAY`), remis à zéro après une session
   productive. Chaque transition est **loguée** — fini le silence.
4. **Gel total du process** : `Type=notify` + `WatchdogSec` → l'app pinge systemd à chaque
   cycle sain ; sans ping, systemd relance (avec `Restart=always`).

> Signature terrain du « silent death » (gateway `active` mais lien mort) : `bluetoothctl
> info <MAC>` → `Connected: no` + silence du journal. C'est exactement ce que la sonde (1)
> et le watchdog (4) éliminent.

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

## Mécanisme retenu

**Client Proxy firmware** (validé par le PoC sur un T114 réel) : le node produit ses
trames MQTT et les pousse sur le lien BLE ; la passerelle les **relaie telles quelles**
au broker. Minimal et robuste — aucun déchiffrement ni reconstruction côté passerelle.

> Repli non retenu : « nodeless republish » (s'abonner à `meshtastic.receive`, reconstruire
> le JSON `msh/<region>/<gwnum>/json/<channel>/<gwid>`). Plus de code ; il aurait servi si
> le Client Proxy over BLE ne fonctionnait pas — mais le PoC a confirmé qu'il fonctionne.

## Démarrer

Voir **[`poc/README.md`](poc/README.md)** pour valider le Client Proxy over BLE sur ton node.

## Licence

AGPL-3.0-or-later — cohérent avec l'écosystème MeshForge.
