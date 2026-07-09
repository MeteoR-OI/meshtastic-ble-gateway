# Résilience & tuning du lien BLE

Le BLE 24/7 est peu fiable, et **meshtastic gèle sur lien mort** dans des appels sans timeout
(`_sendDisconnect`, `disconnect()`…), **impossibles à interrompre en thread** (confirmé py-spy en
prod). La passerelle est donc bâtie en **isolation de process**.

## Isolation de process

- **Worker jetable** : toute la pile BLE tourne dans un **sous-processus**. Sur décrochage (sonde de
  vivacité `is_connected` / `connection.lost`), le worker fait `os._exit` — il saute le teardown qui
  gèle, l'OS récupère tout (threads, event loops, fd).
- **Superviseur** (process principal) : ne touche **jamais** au BLE → ne fige jamais. Il surveille le
  **heartbeat** du worker : worker sorti → respawn ; worker **figé** (heartbeat stagnant au-delà
  d'`alive_timeout`, ou de `connect_grace` pendant la connexion) → **SIGKILL** → respawn. Backoff
  exponentiel plafonné (`MBG_RECONNECT_DELAY` → `MBG_MAX_RECONNECT_DELAY`), remis à zéro après un
  worker qui s'est connecté.
- **Watchdog systemd** (`Type=notify` / `WatchdogSec`) : le superviseur pinge en continu ; systemd ne
  relance le service **que si le superviseur lui-même meurt** — vrai dernier filet.

> Pourquoi pas un simple timeout / monkeypatch ? Chaque point de gel neutralisé en révèle un autre
> (whack-a-mole), et borner `async_await` **fuit un thread daemon + event loop + fd par décrochage**
> (fatal sur armv7 32-bit). On ne peut pas tuer un thread bloqué dans un appel C — mais on peut
> **SIGKILL un process**. D'où l'isolation.

## Disconnect bluez après SIGKILL

Un worker **gelé** tué par SIGKILL **ne ferme pas l'ACL BLE** → `bluetoothd` garde `Connected: yes`
→ le node cesse d'émettre → le worker respawné ne le retrouve pas au scan (`No peripheral found`,
**boucle infinie**). Le worker gelé ne peut pas se nettoyer → **le superviseur force le teardown** :
après chaque `kill()`, il exécute un **`bluetoothctl disconnect <MAC>`** borné par `timeout`
subprocess (préserve l'invariant « le superviseur ne gèle jamais » ; plus sûr qu'un appel D-Bus
in-process). Sur Buster : `bluetoothctl` 5.55 doit être dans le PATH du service
([installation](installation.md#cas-raspbian-buster)).

> Effet : reconnexion **automatique** après un gel, au lieu d'une récupération manuelle.

## Stabilisation du lien BLE (signal faible)

**Opt-in** (`MBG_BLE_SUPERVISION_TIMEOUT_MS=6000`, défaut `0` = off). Sur un lien faible
(**-80/-90 dBm**), le node « churn » (coupe/relance toutes les 2-3 min). Diagnostic terrain : la
coupure est un **supervision timeout** BLE (`reason 0x08`) — temps max sans paquet reçu avant que le
lien soit déclaré mort. Le défaut BlueZ (RPi = *central*) est **420 ms** ; à -80/-90 dBm, ~8 paquets
manqués (~0,4 s de fading) suffisent à couper.

Le node préférerait 2 s, mais **le central décide**, et BlueZ 5.55 **ignore** la debugfs
`supervision_timeout` en central (bug [bluez #717](https://github.com/bluez/bluez/issues/717),
vérifié via `btmon`). Le seul levier qui tienne est une **`LE Connection Update` initiée par le
central sur le lien vivant** (`hcitool lecup`). Comme **chaque connexion BLE = une session worker**,
on impose le supervision timeout **une fois par session**, dès le lien établi — pas de polling, et le
respawn couvre naturellement chaque reconnexion.

- **Effet mesuré terrain** : churn **~19-27 reconnexions/h → ~1,5/h** à 6 s de timeout (**~94 %** de
  churn en moins), zéro `reason 0x08` résiduel. Se lit dans le compteur `link_quality`
  ([monitoring](monitoring.md)).
- **Prérequis** — sinon l'application échoue, est loguée, et la session continue **sans le réglage**
  (au pire on retombe sur le churn d'origine) :
  - `hcitool` installé (`apt install bluez`) ;
  - capabilities sur le service — **décommenter les 3 lignes ensemble** dans
    [`deploy/mbg.service`](../deploy/mbg.service) :
    ```ini
    Environment=MBG_BLE_SUPERVISION_TIMEOUT_MS=6000
    AmbientCapabilities=CAP_NET_ADMIN CAP_NET_RAW
    CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_RAW
    ```
    puis `systemctl daemon-reload && systemctl restart mbg`.

> ⚠️ `CAP_NET_ADMIN` est large (admin réseau) : c'est le coût de garder le réglage dans le worker
> (testable à 100 %, une fois par session) plutôt qu'un service root séparé. Sur un RPi dédié
> passerelle, acceptable.
>
> **Si le lien reste sous ~-95 dBm**, le levier devient **RF/matériel** : dongle USB BLE à antenne
> externe côté RPi (+6-15 dB), ou firmware node `NRF52_BLE_TX_POWER 8` (+8 dB).

Variables : [configuration.md](configuration.md). Détail des modules : [architecture.md](architecture.md).
