# PoC — MQTT Client Proxy over BLE

Objectif : **valider empiriquement** que le MQTT Client Proxy Meshtastic fonctionne
en **Bluetooth** sur un T114 réel. La communauté le déconseille sur BLE
([LN4CY/mqtt-proxy](https://github.com/LN4CY/mqtt-proxy),
[MeshMonitor](https://meshmonitor.org/add-ons/mqtt-proxy) → TCP/Serial), mais notre
node cible est **BT-only**, donc on teste. Si ça marche → cœur de la V0.1. Sinon →
repli « nodeless republish JSON » (voir le README racine).

> Spike volontairement minimal, **non couvert par les tests** : sa valeur est la
> validation matérielle (BLE réel + firmware), hors CI.

## Prérequis

```bash
pip install meshtastic paho-mqtt
```

Le node de test : **T114, BLE MAC `E6:E3:53:4B:BE:A5`**.

## 1. Vérifier la config du node (READ-ONLY — ne rien changer)

```bash
meshtastic --ble E6:E3:53:4B:BE:A5 --info
```

Confirmer dans la sortie que le node est **déjà** prêt (normalement oui) :

| Réglage | Valeur attendue |
|---------|-----------------|
| `mqtt.enabled` | `true` |
| `mqtt.proxy_to_client_enabled` | `true` |
| `mqtt.json_enabled` | `true` (→ le proxy émet du `/json/`, ingéré direct par MeshForge) |
| un canal `uplink_enabled` | `true` |

Si un flag manque, **ne pas le modifier** : le signaler. En mode Client Proxy,
c'est le PoC (`--broker`) qui choisit le broker cible — le champ `mqtt.address` du
node n'entre pas en jeu.

## 2. Broker d'observation local

```bash
docker compose -f poc/docker-compose.yml up -d
# dans un autre terminal, on regarde tout ce qui arrive :
mosquitto_sub -h localhost -t 'msh/#' -v
```

## 3. Lancer le PoC

Depuis le Mac (BLE via CoreBluetooth) ou depuis le RPi (`hci0`) :

```bash
python poc/ble_mqtt_proxy.py --ble E6:E3:53:4B:BE:A5 --broker localhost
```

Le PoC monopolise le lien BLE (Meshtastic = 1 client à la fois) : s'assurer
qu'aucune app téléphone n'est connectée au node.

## 4. Résultat attendu

Dès que le node produit du trafic (télémétrie/position périodique, ou un message
test), des lignes `msh/EU_868/.../json/...` apparaissent **et** dans
`mosquitto_sub`, **et** dans les logs du PoC (`[uplink] ...`).
→ **Client Proxy over BLE validé.**

Test bout-en-bout MeshForge (option) : pointer `--broker` vers le Mosquitto de
MeshForge et ajouter le nom du canal à l'allowlist `public_channels` (admin) ; le
node doit apparaître sur la carte.

Si **rien** ne remonte alors que le node émet bien du trafic → Client Proxy over
BLE non fonctionnel → bascule sur le repli **nodeless** (README racine).

## Options

| Flag | Rôle |
|------|------|
| `--ble` | MAC/nom BLE du node (défaut `E6:E3:53:4B:BE:A5`) |
| `--broker` / `--port` | broker MQTT cible (défaut `localhost:1883`) |
| `--username` / `--password` | auth broker (optionnel) |
| `--downlink` | active broker→node (⚠️ boucle d'écho si abonné à ses propres uplinks) |
| `-v` | logs debug |
