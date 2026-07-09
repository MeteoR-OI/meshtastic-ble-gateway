# Dépannage & caveats

## Le node se connecte mais aucune donnée ne remonte (Buster)

**BlueZ 5.50** ne gère pas les notifications GATT attendues par bleak/meshtastic. → installer
**BlueZ 5.55** (`bluez-meshforge`) et mettre `/opt/bluez-5.55/bin` en tête du `PATH` du service.
Voir [installation](installation.md#cas-raspbian-buster).

## Boucle `No peripheral found` / churn permanent

- **Node `Trusted`** : un node *trusted* est auto-reconnecté par `bluetoothd` → il cesse d'émettre
  → le scan ne le trouve plus. Le garder **`Paired` mais NON `Trusted`** :
  `bluetoothctl untrust <MAC-du-node>` (l'appairage reste).
- **Après un gel** : depuis v0.6.1, le superviseur force un `bluetoothctl disconnect` après SIGKILL
  (nécessite `bluetoothctl` dans le PATH). Voir [resilience.md](resilience.md#disconnect-bluez-après-sigkill).

## Churn de reconnexion sur signal faible (-80/-90 dBm)

Supervision timeout BLE trop court côté central (420 ms). → activer la **stabilisation du lien**
(`MBG_BLE_SUPERVISION_TIMEOUT_MS`, + capabilities). Voir
[resilience.md](resilience.md#stabilisation-du-lien-ble-signal-faible). Sous ~-95 dBm : levier
**RF/matériel** (antenne externe, TX power node).

## `Address already in use` au démarrage de l'API

Le port **`8080` peut entrer en conflit** avec un nginx présent sur l'hôte. → choisir un port libre
(`MBG_API_PORT=8791` par ex.). Voir [configuration.md](configuration.md#api-de-contrôle-opt-in).

## `/metrics` renvoie `"node": null`

Aucun relevé encore écrit (sonde vient de démarrer, ou lien jamais établi assez longtemps). Un
relevé est fait **tôt dans chaque session** ; attendre une connexion réussie. Le monitoring doit
être activé (`MBG_MONITOR_INTERVAL > 0`). Voir [monitoring.md](monitoring.md).

## L'API renvoie `503` / `504`

- `503` : aucun worker connecté (déconnexion BLE en cours, ou fenêtre OFF d'un duty-cycle
  [paliers batterie](battery-tiers.md)).
- `504` : le worker n'a pas répondu dans `MBG_CONTROL_TIMEOUT` (write BLE lent/gelé → il sera
  SIGKILL par l'isolation).

## L'ACK `want_ack` n'apparaît pas dans la réponse HTTP

C'est **normal** : la réponse HTTP est immédiate (`ok`) ; l'ACK/NAK arrive **plus tard** dans le
journal (`[downlink] ACK … → reçu/échec`). L'`onResponse` de meshtastic BLE 2.7.10 est cassé → l'ACK
est corrélé via `meshtastic.receive`. Voir [api.md](api.md).

## La position d'un node distant ne « revient » pas tout de suite

Les requêtes distantes (`dest`) sont **asynchrones** : la réponse du node distant transite par le
mesh et **remonte en `[uplink]` MQTT** (pas dans la réponse HTTP), quelques secondes plus tard — si
le node est joignable/éveillé. Voir [api.md](api.md).

## Pas de RSSI du lien BLE dans la sonde

Inobtenable sur BlueZ pour un lien LE **connecté** (contrôleur détenu par `bluetoothd`). Le signal
de qualité BLE est le **compteur de reconnexions** (`link_quality`). Voir [monitoring.md](monitoring.md).

## `git pull` ne met pas le code à jour

L'install doit être **éditable** (`pip install -e .`). En non-éditable, `git pull` ne recharge pas
le code. Voir [installation](installation.md#1-installer-la-passerelle).

## `Software caused connection abort` transitoire

Normal à faible signal pendant un `--export-config` ou une connexion : réessayer.
