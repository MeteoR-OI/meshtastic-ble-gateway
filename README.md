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
| **V0.2** | API de contrôle / downlink (envoi texte, télémétrie, admin node) | ✅ |
| **V0.3** | Monitoring : sonde SQLite (métriques node + qualité BLE), API + export CSV | ✅ |
| **V0.4** | Paliers batterie + duty-cycle du lien BLE (adaptatif selon la batterie du node) | ✅ |
| **V0.5** | Stabilisation du lien BLE sur signal faible (supervision timeout via `hcitool lecup`) | ✅ |
| **V0.6** | Support **Raspbian Buster** (Python/BlueZ isolés, pin bleak) + **requêtes vers un node distant** | ✅ |
| **V0.7** | Transports alternatifs (USB-série / WiFi-TCP) si le matériel le permet | ⏳ |

## API de contrôle (downlink)

Puisque la passerelle **monopolise le BLE** (1 client à la fois), elle est le seul moyen
de parler au node pendant qu'elle tourne. Une **API HTTP à token** (activée si
`MBG_API_TOKEN` défini) permet d'**envoyer du texte** (canal public ou privé), **de la
télémétrie**, **de forcer une diffusion de position** (rafraîchir la carte sans attendre le
cycle de 12 h), **d'interroger un node distant** (requête télémétrie/position `wantResponse`,
la réponse remonte en `[uplink]` MQTT), et **d'administrer le node** (rôle, intervalles…).
Détails, endpoints et sécurité : voir [`deploy/README.md`](deploy/README.md).

Les commandes passent par le worker (écriture BLE) ; un write qui gèle est absorbé par
l'isolation (worker SIGKILL → 503/504). C'est le seul point qui **rompt le « receive-only »**.

## Monitoring / sonde (V0.3)

Activée si `MBG_MONITOR_INTERVAL > 0`, la sonde historise en **SQLite local** (stdlib, zéro
dépendance, mode WAL) les métriques du node **et** la qualité du lien BLE — base des paliers
batterie (V0.4). Point clé : **lecture batterie ACTIVE** locale (`getMyNodeInfo`), qui
contourne le broadcast télémétrie de 12 h du node → mesure toujours fraîche.

- **Worker** (dans le sous-processus BLE) : `node_metrics` (batterie, voltage, util. canal/air,
  uptime), `position`, `neighbors` directs + SNR. Un `getMyNodeInfo` qui gèle = worker SIGKILL
  (même isolation que tout appel BLE). `MBG_MONITOR_FORCE_TELEMETRY=true` force un `sendTelemetry`
  avant lecture si le firmware ne rafraîchit pas passivement.
- **Superviseur** : `link_quality` (compteur de reconnexions — le vrai signal de qualité BLE),
  + thread d'export **CSV** périodique (`MBG_DUMP_DIR`) et purge (`MBG_RETENTION_DAYS`).
- **API** (même token que le contrôle) : `GET /metrics` (dernier relevé node + lien),
  `GET /history?since=&limit=`. Consommable aussi en lisant directement le fichier SQLite.

Variables (`MBG_DB_PATH`, `MBG_MONITOR_INTERVAL`, `MBG_DUMP_DIR`…) et exemples curl :
voir [`deploy/README.md`](deploy/README.md).

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
  `MBG_MAX_RECONNECT_DELAY`), remis à zéro après un worker qui s'est connecté. Après un
  SIGKILL, le superviseur **force un `disconnect` bluez** du node (le worker gelé n'a pas fermé
  l'ACL ; sans ça `bluetoothd` garde le lien, le node cesse d'émettre et le respawn ne le
  retrouve pas — boucle infinie observée en prod). Appel borné (`timeout`) → le superviseur ne fige jamais.
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

### Paliers batterie (V0.4)

**Opt-in** (`MBG_BATTERY_TIERS=true`, nécessite le monitoring). Le superviseur lit la batterie
du node (sonde V0.3) et adapte le comportement — plus la batterie baisse, moins on sollicite
le lien, pour préserver **la batterie du node** (un lien BLE permanent empêche son light-sleep).

| Batterie | Monitoring | Lien BLE / proxy |
|----------|-----------|------------------|
| ≥ 75 % | relevé 15 min | connecté, proxy live |
| ≥ 50 % | relevé 30 min | connecté, proxy live |
| ≥ 25 % | relevé 60 min | connecté, proxy live |
| < 25 % | 1 relevé/fenêtre | **duty-cycle** `MBG_DUTY_ON` 5 min / `MBG_DUTY_OFF` 30 min (⚠️ trous de flux assumés) |

- **Duty-cycle (< 25 %)** : le lien est **volontairement coupé** pendant le OFF (le node peut
  dormir) → uplinks perdus sur cette fenêtre. Le OFF est une attente **watchdog-friendly** (il
  dépasse `WatchdogSec`, donc le superviseur continue de pinger systemd). Pendant le OFF,
  `/metrics` reste servi mais l'API de contrôle renvoie `503` (aucun worker).
- **Hystérésis** (`MBG_TIER_HYSTERESIS`, 3 % par défaut) : on descend d'un palier au seuil
  nominal mais on ne remonte qu'après seuil + hystérésis → pas de flapping (surtout autour du
  seuil critique 25 %).
- **Télémétrie au changement de mode** : à chaque transition de palier, la session suivante
  **force un `sendTelemetry`** (broadcast) → la batterie fraîche est diffusée sur le mesh /
  MeshForge, annonçant le changement. Détails/variables : [`deploy/README.md`](deploy/README.md).

### Stabilisation du lien BLE (V0.5)

**Opt-in** (`MBG_BLE_SUPERVISION_TIMEOUT_MS=6000`, défaut 0 = off). Sur un lien faible
(**-80/-90 dBm**), le node « churn » (coupe/relance toutes les 2-3 min). Diagnostic terrain :
la coupure est un **supervision timeout** BLE (`reason 0x08`) — temps max sans paquet reçu avant
que le lien soit déclaré mort. Le défaut BlueZ (RPi = *central*) est **420 ms** ; à -80/-90 dBm,
~8 paquets manqués (~0,4 s de fading) suffisent à couper.

Le node préférerait 2 s, mais **le central décide**, et BlueZ 5.55 **ignore** la debugfs
`supervision_timeout` en central (bug [bluez #717](https://github.com/bluez/bluez/issues/717),
vérifié via `btmon`). Le seul levier qui tienne est une **`LE Connection Update` initiée par le
central sur le lien vivant** (`hcitool lecup`). Comme **chaque connexion BLE = une session
worker**, on impose le supervision timeout **une fois par session**, dès le lien établi — pas de
polling, et le respawn couvre naturellement chaque reconnexion.

- **Effet mesuré terrain** : churn **~19-27 reconnexions/h → ~1,5/h** à 6 s de timeout
  (**~94 %** de churn en moins), zéro `reason 0x08` résiduel. Se lit directement dans le compteur
  `link_quality` de la sonde (V0.3).
- **Prérequis** : `CAP_NET_ADMIN` + `CAP_NET_RAW` sur le service (émission d'une commande HCI) et
  `hcitool` installé (paquet `bluez`). Voir le bloc dédié dans [`deploy/mbg.service`](deploy/mbg.service).
- **Sûreté** : `link_tuner.tune_link` **ne lève jamais** — droits manquants, node déconnecté ou
  `hcitool` absent sont logués, la session continue (au pire on retombe sur le churn d'origine).
- **Si insuffisant** (lien durablement sous ~-95 dBm) : le levier devient **RF/matériel** — dongle
  USB BLE à antenne externe côté RPi (+6-15 dB), ou firmware node `NRF52_BLE_TX_POWER 8` (+8 dB).

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
