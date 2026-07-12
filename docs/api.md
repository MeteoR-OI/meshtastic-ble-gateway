# API de contrôle (downlink)

Puisque la passerelle **monopolise le BLE** (1 client à la fois), elle est le seul moyen de
parler au node pendant qu'elle tourne. Une **API HTTP à token** (opt-in) permet d'envoyer du
texte, de la télémétrie/position, d'interroger un node distant et d'administrer le node.

> C'est le **seul point qui rompt le « receive-only »** : les commandes passent par le worker
> (écriture BLE) ; un write qui gèle est absorbé par l'isolation (worker SIGKILL → `503`/`504`).

## Activation & sécurité

Activée **uniquement si `MBG_API_TOKEN` est défini**. Auth par en-tête `X-API-Token`.
Sécurité v1 : token + bind LAN/VPN (`MBG_API_HOST=127.0.0.1` pour du localhost strict). Voir
[configuration.md](configuration.md#api-de-contrôle-opt-in).

```bash
TOKEN=…              # = MBG_API_TOKEN
BASE=http://<hote-passerelle>:8080
```

## Endpoints

| Méthode | Route | Rôle |
|---|---|---|
| `POST` | `/send/text` | message texte sur un canal (`want_ack` optionnel) |
| `POST` | `/send/telemetry` | télémétrie du node **local** ; avec `dest` → **requête** à un node distant |
| `POST` | `/send/position` | (re)diffuse la position **fixe** du node (jamais `0,0`) |
| `POST` | `/request/position` | **requête** de position à un node **distant** (`dest` requis) |
| `POST` | `/admin` | réglage curaté du node (`role`, `position_broadcast_secs`, `gps_mode`, `device_update_interval`) |
| `GET` | `/health` | ping |
| `GET` | `/info` | découverte : `version`, `node_id`, `node_name`, `monitor_interval`, `battery_tiers`, `broker`, `mqtt_proxy_ok`, `map_reporting` |
| `GET` | `/metrics` | dernier relevé de la sonde ([monitoring.md](monitoring.md)) |
| `GET` | `/history` | série `node_metrics` |

`GET /info` renvoie la **version** de la passerelle + l'**identité du node** (id + nom humain, dès
qu'un relevé a été fait) + quelques réglages — utile pour la découverte (ex. tuile d'un installateur).
`GET /metrics` inclut `node.node_id`/`node.node_name` et un agrégat `neighbors`
([monitoring.md](monitoring.md)) — **portée & voisinage** (V0.8.1), calculé sur des données déjà
remontées (aucune op BLE) :

```json
"neighbors": {
  "count": 7, "best_snr": 7.25,
  "max_distance_km": 12.4,   // haversine passerelle↔voisin le plus lointain (km, 0,1) ; null si aucune position
  "distinct_1h": 5,          // voisins distincts (COUNT DISTINCT node_id) entendus sur 1 h
  "distinct_24h": 9,         // idem sur 24 h
  "distinct_total": 23       // idem sur tout l'historique
}
```

`max_distance_km` provient du dernier relevé de la sonde (position passerelle `node_metrics.lat/lon`
↔ position des voisins lue localement dans la NodeDB) ; `null` si la passerelle ou tous les voisins
n'ont pas de position. Le bloc `neighbors` vaut `null` tant qu'aucun voisin n'a été relevé.

`GET /info` expose aussi le **statut d'onboarding** du node (consommé par l'intégration WeeWX) :
`broker` (l'`address` MQTT configurée sur le node), `mqtt_proxy_ok` (module MQTT activé **et**
proxy client activé — la paire qui fait remonter le trafic via la passerelle) et `map_reporting`
(`map_reporting_enabled`). Ces champs sont lus de la **config MQTT locale du node** par la sonde
(un relevé en début de chaque session BLE, puis à la cadence du monitoring) : ils valent `null`
si le monitoring est désactivé (`MBG_MONITOR_INTERVAL=0`) ou si aucun relevé n'a encore eu lieu.
Pour configurer le node lui-même, voir [provision.md](provision.md).

## Exemples

```bash
# texte sur un canal (nom ou index) :
curl -H "X-API-Token: $TOKEN" -d '{"text":"alerte","channel":"MonCanal"}' $BASE/send/text
# avec accusé d'émission radio (want_ack) -> log ASYNCHRONE [downlink] ACK … → reçu/échec :
curl -H "X-API-Token: $TOKEN" -d '{"text":"test","channel":"MonCanal","want_ack":true}' $BASE/send/text

# télémétrie du node local (diffusion) :
curl -H "X-API-Token: $TOKEN" -d '{}' $BASE/send/telemetry
# (re)diffuser la position fixe du node (rafraîchit la carte sans attendre le cycle 12 h) :
curl -H "X-API-Token: $TOKEN" -X POST $BASE/send/position
curl -H "X-API-Token: $TOKEN" -d '{"lat":48.85,"lon":2.35,"alt":35}' $BASE/send/position  # override explicite

# INTERROGER un node DISTANT (wantResponse ; la réponse remonte en [uplink] MQTT) :
curl -H "X-API-Token: $TOKEN" -d '{"dest":"!a1b2c3d4"}' $BASE/send/telemetry   # télémétrie distante
curl -H "X-API-Token: $TOKEN" -d '{"dest":"!a1b2c3d4"}' $BASE/request/position  # position distante

# admin :
curl -H "X-API-Token: $TOKEN" -d '{"setting":"position_broadcast_secs","value":43200}' $BASE/admin
curl -H "X-API-Token: $TOKEN" $BASE/health
```

## Codes de réponse

`200` ok · `401` token invalide · `503` aucun worker connecté · `504` timeout worker ·
`400` commande invalide.

## Détails de comportement

- **Requêtes vers un node distant** (`dest` sur `/send/telemetry`, ou `/request/position`) :
  envoie une requête **`wantResponse`** au node ciblé (≈ `meshtastic --request-telemetry` /
  `--request-position`). La réponse arrive **de façon asynchrone via le mesh → elle remonte en
  `[uplink]` MQTT** (pas dans la réponse HTTP). Utile pour rafraîchir un node distant sans attendre
  son cycle passif.
- **`/send/position`** fournit **toujours** des coordonnées : sans payload il relit la position
  **fixe** du node et la ré-émet. C'est volontaire — `sendPosition()` sans coords émettrait `0,0`,
  que le firmware **adopterait comme position locale** (écrasant la position fixe). Node sans
  position connue → `400`.
- **`want_ack`** (`/send/text`) : la réponse HTTP reste immédiate (`ok`) ; l'ACK/NAK arrive **plus
  tard** dans le journal (`[downlink] ACK canal=… → reçu (ACK)` / `échec`). Broadcast → ACK
  implicite (un voisin rebroadcaste).
- **Audit** : toutes les commandes sont tracées en INFO — `[downlink] …` (superviseur) et
  `[downlink] ACK …` (asynchrone, worker). Les réponses des nodes distants apparaissent en `[uplink]`.

Whitelist admin extensible dans [`src/mbg/control.py`](../src/mbg/control.py).
