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

### Résilience (BLE instable) — isolation de process

Le BLE décroche souvent, et **meshtastic gèle sur lien mort** dans des appels sans timeout
(`_sendDisconnect`, `disconnect()`…), **impossibles à interrompre en thread** (confirmé
py-spy en prod). La passerelle est donc bâtie en **isolation de process** :

- **Worker jetable** : toute la pile BLE tourne dans un **sous-processus**. Sur décrochage
  (sonde de vivacité `is_connected` / `connection.lost`), le worker fait `os._exit` — il
  saute le teardown qui gèle, l'OS récupère tout (threads, event loops, fd).
- **Superviseur** (process principal) : ne touche **jamais** au BLE → ne fige jamais. Il
  surveille le **heartbeat** du worker : worker sorti → respawn ; worker **figé** (heartbeat
  stagnant au-delà d'`alive_timeout`, ou de `connect_grace` pendant la connexion) →
  **SIGKILL** → respawn. Backoff exponentiel plafonné (`MBG_RECONNECT_DELAY` →
  `MBG_MAX_RECONNECT_DELAY`), remis à zéro après un worker qui s'est connecté.
- **Watchdog systemd** (`Type=notify` / `WatchdogSec`) : le superviseur pinge en continu ;
  systemd ne relance le service **que si le superviseur lui-même meurt** — vrai dernier filet.

> Pourquoi pas un simple timeout / monkeypatch ? Chaque point de gel neutralisé en révèle
> un autre (whack-a-mole), et borner `async_await` **fuit un thread daemon + event loop +
> fd par décrochage** (fatal sur armv7 32-bit). On ne peut pas tuer un thread bloqué dans
> un appel C — mais on peut **SIGKILL un process**. D'où l'isolation.

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
