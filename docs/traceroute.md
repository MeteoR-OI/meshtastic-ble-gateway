# Traceroute (endpoint + planificateur automatique)

La passerelle peut **tracer la route mesh** vers un node distant (portnum `TRACEROUTE_APP`) : le
chemin aller (et retour si le firmware distant le renseigne), avec le SNR de chaque saut. Deux
usages :

1. **Endpoint HTTP `POST /traceroute`** — à la demande (async ou bloquant).
2. **Planificateur automatique** (opt-in) — quelques traceroute/jour, cible choisie par politique.

> **Sans coupure BLE.** L'émission ET la corrélation de la réponse s'exécutent **dans le worker
> qui tient déjà l'interface BLE** — jamais de 2ᵉ connexion ni de CLI `meshtastic --ble`. La
> réponse arrive dans la boucle de réception existante (`meshtastic.receive`), au même titre que
> les uplinks : elle ne bloque ni le relais ni le heartbeat.

> **Parcimonie airtime.** Le traceroute Meshtastic est un *flood* coûteux, rate-limité par le
> firmware. Les défauts sont **délibérément conservateurs** (budget quotidien, intervalle minimal
> global et par nœud, heures calmes, garde d'occupation canal). Le planificateur vise **quelques
> traceroute/jour**, jamais un débit soutenu.

---

## Endpoint `POST /traceroute`

Disponible dès que l'**API est activée** (`MBG_API_TOKEN` posé) — indépendamment du planificateur.
Authentification par en-tête `X-API-Token`, comme les autres routes.

### Corps de requête

```jsonc
{
  "dest": "!6984ddb0",   // requis : "!hex", "hex" ou num int
  "hop_limit": 7,         // optionnel, défaut 7, borné [1..7]
  "channel_index": 0,     // optionnel, défaut 0
  "wait": false,          // optionnel : true = bloquant, false = async (défaut)
  "timeout_s": 30         // optionnel, défaut 30, borné [5..60]
}
```

### Réponses

| Mode | Code | Corps |
|---|---|---|
| **async** (`wait:false`, défaut) | `202` | `{"status":"accepted","dest":"!6984ddb0","request_id":123456789}` |
| **bloquant** (`wait:true`), réponse reçue | `200` | le résultat complet (voir plus bas) |
| **bloquant**, délai dépassé | `504` | `{"status":"timeout","dest":…,"request_id":…}` |
| validation | `400` | `{"ok":false,"error":"…"}` (dest invalide, hop_limit hors bornes…) |
| aucun worker connecté | `503` | `{"ok":false,"error":"aucun worker connecté"}` |

En mode **async**, le résultat part de toute façon en **MQTT** + est écrit en **SQLite** dès qu'il
arrive (ou au timeout). Le mode **bloquant** relit simplement la ligne SQLite (base WAL partagée)
jusqu'au résultat — il **ne bloque pas** le worker.

### Exemples `curl`

```bash
TOKEN=…            # MBG_API_TOKEN
API=127.0.0.1:8791

# async : accusé immédiat, résultat en MQTT + /history
curl -s -X POST "$API/traceroute" -H "X-API-Token: $TOKEN" \
     -d '{"dest":"!6984ddb0"}'

# bloquant : attend la route (jusqu'à timeout_s)
curl -s -X POST "$API/traceroute" -H "X-API-Token: $TOKEN" \
     -d '{"dest":"!6984ddb0","wait":true,"timeout_s":30}'
```

### Format du résultat (MQTT + `wait:true` + `/history`)

Publié sur le topic `MBG_TRACEROUTE_TOPIC` (défaut `mbg/traceroute`).

> ⚠️ **ACL broker MeshForge.** Le compte MQTT d'une station est en général **publish-only et
> scopé à `msh/#`** : un publish vers `mbg/traceroute` est alors **rejeté** par le broker
> (`Not authorized`), silencieusement côté passerelle (publish QoS 0 → aucune erreur loguée ;
> le résultat reste néanmoins en SQLite / `/history`). Sur un tel broker, **surcharge le topic**
> vers le namespace autorisé, **sans** segment `msh/<région>/2/…` pour ne pas être ingéré par
> MeshForge comme du trafic meshtastic — p.ex. :
> `MBG_TRACEROUTE_TOPIC=msh/EU_868/mbg/traceroute/!<nodeid>`.
> (Vérifié en prod sur `mqtt-mt.meteor-oi.re` : `mbg/traceroute` → `Not authorized`,
> `msh/EU_868/mbg/…` → accepté.)

```json
{
  "type": "traceroute",
  "gateway_node": "!362e105b",
  "dest": "!6984ddb0",
  "request_id": 123456789,
  "status": "ok",
  "sent_ts": "2026-07-14T18:50:00Z",
  "recv_ts": "2026-07-14T18:50:04Z",
  "rtt_ms": 4120,
  "hop_limit": 7,
  "hops_to": 2,
  "hops_back": 2,
  "route_to":   [ {"node":"!362e105b","snr":null},
                  {"node":"!aabbccdd","snr":-8.5},
                  {"node":"!6984ddb0","snr":-6.0} ],
  "route_back": [ {"node":"!6984ddb0","snr":null},
                  {"node":"!aabbccdd","snr":-7.0},
                  {"node":"!362e105b","snr":-5.5} ],
  "source": "api"
}
```

- `route_to` = chemin aller complet `[passerelle, *relais, dest]` ; le 1er saut (origine) a
  `snr:null`. `route_back` = chemin retour (présent seulement si le firmware distant le renseigne,
  sinon `null`). SNR en **dB** (`valeur firmware / 4` ; sentinelle « inconnu » → `null`).
- `status` : `ok` (route reçue) · `timeout` (pas de réponse dans le délai) · `error` (échec
  d'émission — BLE down — ou réponse illisible).
- `source` : `api` (endpoint) ou `scheduler:<policy>` (planificateur).

### Historique & compteurs

- `GET /history?type=traceroute&limit=100` → `{"rows":[ …résultats… ]}` (les N derniers, DESC).
  Indépendant du monitoring.
- `GET /metrics` (nécessite le monitoring) inclut un bloc `traceroute` :
  `traceroute_sent_total`, `traceroute_ok_total`, `traceroute_timeout_total`,
  `traceroute_error_total`, `traceroute_last_rtt_ms`.

---

## Planificateur automatique (opt-in)

Activé par `MBG_TRACEROUTE_ENABLED=true` (**désactivé par défaut** ; on l'active station par
station après validation banc). À chaque *tick* (`MBG_TRACEROUTE_TICK_S`), il applique les
garde-fous puis choisit **au plus une** cible selon la politique, et réutilise **exactement le même
chemin d'émission** que l'endpoint (`source = scheduler:<policy>`).

### Politiques

| `MBG_TRACEROUTE_POLICY` | Sélection |
|---|---|
| `static` | rotation **round-robin** sur `MBG_TRACEROUTE_TARGETS` (déterministe). |
| `staleness` *(défaut)* | parmi les nodes **entendus récemment** (`MBG_TRACEROUTE_RECENT_H`) et hors cooldown, celui dont le **dernier traceroute réussi est le plus ancien** (jamais tracé = priorité max). Maximise la fraîcheur de la carte pour un budget fixe. Pondération facultative via `MBG_TRACEROUTE_PRIORITY`. |

`recent` et `adaptive` sont prévus comme points d'extension (registre `_POLICIES`) — à ajouter sans
refactor.

**Quel mode choisir ?** `staleness` (défaut) pour une station en exploitation : il couvre
automatiquement les nodes réellement entendus, en priorisant ceux qu'on n'a pas tracés depuis
longtemps → carte fraîche sans liste à maintenir. `static` pour un banc / un diagnostic ciblé : tu
imposes exactement les nodes à tracer (liste `MBG_TRACEROUTE_TARGETS` **obligatoire**), en rotation.

### Configurer (drop-in systemd)

L'activation se fait par variables d'env dans un **drop-in** de l'unité (le service lance
`python -m mbg` sans argument → tout vient de l'ENV). Créer/éditer
`/etc/systemd/system/mbg.service.d/traceroute.conf` puis `sudo systemctl daemon-reload && sudo
systemctl restart mbg`.

**Mode `staleness` (recommandé, budget bas pour démarrer)** :
```ini
[Service]
Environment=MBG_TRACEROUTE_ENABLED=true
Environment=MBG_TRACEROUTE_POLICY=staleness
Environment=MBG_TRACEROUTE_DAILY_BUDGET=4
# topic MQTT dans le namespace autorisé par le broker (cf. encadré ACL plus haut) :
Environment=MBG_TRACEROUTE_TOPIC=msh/EU_868/mbg/traceroute/!534bbea5
```

**Mode `static` (cibles imposées)** :
```ini
[Service]
Environment=MBG_TRACEROUTE_ENABLED=true
Environment=MBG_TRACEROUTE_POLICY=static
Environment=MBG_TRACEROUTE_TARGETS=!6984ddb0,!d1062139
Environment=MBG_TRACEROUTE_DAILY_BUDGET=6
Environment=MBG_TRACEROUTE_TOPIC=msh/EU_868/mbg/traceroute/!534bbea5
```

**Désactiver le planificateur (endpoint manuel seul)** : retirer/mettre `MBG_TRACEROUTE_ENABLED=false`
— `POST /traceroute` reste disponible tant que l'API a un token.

Vérifier l'application : `systemctl show mbg -p Environment` puis, après un tick, la présence de
lignes `source: scheduler:<policy>` dans `GET /history?type=traceroute`.

> **Cadence réelle vs budget.** Le min-gap est élargi par un *jitter* aléatoire : l'écart effectif
> entre deux traceroute est `MBG_TRACEROUTE_MIN_GAP_S × [1, 2)` (anti heure-ronde). Avec les défauts
> (min-gap 15 min → 15–30 min effectifs) et la fenêtre active, le nombre réel de traceroute/jour peut
> être **inférieur** au budget : le budget est un **plafond**, pas une cible.

### Garde-fous (toutes politiques)

- **Intervalle min global** `MBG_TRACEROUTE_MIN_GAP_S` (+ un *jitter* aléatoire borné pour ne pas
  taper à l'heure ronde).
- **Intervalle min par nœud** `MBG_TRACEROUTE_PER_NODE_MIN_S`.
- **Budget quotidien** `MBG_TRACEROUTE_DAILY_BUDGET` (reset à minuit, fuseau station).
- **Heures calmes** `MBG_TRACEROUTE_QUIET_HOURS` (`HH:MM-HH:MM`, fuseau station ; gère minuit).
- **Garde occupation canal** `MBG_TRACEROUTE_MAX_CHANUTIL` : si le `channel_utilization` du node
  local dépasse le seuil, on saute le tick (évite de saturer un mesh déjà chargé). Inconnu → pas de
  garde.
- **BLE down** → skip propre (statut `error`, jamais bloquant).

L'état (dernier traceroute par nœud, budget du jour) **persiste en SQLite** → survit aux restarts.

---

## Variables d'environnement

| Variable | Défaut | Rôle |
|---|---|---|
| `MBG_TRACEROUTE_ENABLED` | `false` | active le **planificateur** (l'endpoint marche sans, dès que l'API est ouverte) |
| `MBG_TRACEROUTE_POLICY` | `staleness` | `static` \| `staleness` |
| `MBG_TRACEROUTE_DAILY_BUDGET` | `6` | nb max de traceroute auto / jour |
| `MBG_TRACEROUTE_HOP_LIMIT` | `7` | hop_limit par défaut (borné [1..7]) |
| `MBG_TRACEROUTE_TARGETS` | – | liste `!hex,!hex` (**requis si** `policy=static`) |
| `MBG_TRACEROUTE_RECENT_H` | `24` | `policy=staleness` : fenêtre « entendu récemment » (h) |
| `MBG_TRACEROUTE_PER_NODE_MIN_S` | `21600` | intervalle min par nœud (défaut 6 h) |
| `MBG_TRACEROUTE_MIN_GAP_S` | `900` | intervalle min global (défaut 15 min) |
| `MBG_TRACEROUTE_QUIET_HOURS` | `22:00-06:00` | plage sans émission (fuseau station ; vide = aucune) |
| `MBG_TRACEROUTE_MAX_CHANUTIL` | `40` | skip le tick si `channel_utilization` local dépasse (%) |
| `MBG_TRACEROUTE_PRIORITY` | – | `policy=staleness` : nœuds prioritaires (facultatif) |
| `MBG_TRACEROUTE_TICK_S` | `300` | période d'évaluation du planificateur (s) |
| `MBG_TRACEROUTE_TOPIC` | `mbg/traceroute` | topic MQTT de publication du résultat. ⚠️ sur un broker MeshForge (ACL `msh/#`), surcharger vers `msh/<région>/mbg/traceroute/!<nodeid>` (cf. encadré ci-dessus) |

> **Store SQLite.** L'endpoint et le planificateur écrivent l'historique dans la même base
> (`MBG_DB_PATH`). Elle est créée dès que l'API est ouverte OU que le planificateur est actif, même
> si le monitoring (`MBG_MONITOR_INTERVAL`) est désactivé. `GET /metrics` reste néanmoins gouverné
> par le monitoring (les compteurs traceroute n'y apparaissent que s'il est actif).

> **Rate-limit firmware.** Garder budget + min-gap **très en deçà** des limites Meshtastic. Les
> défauts ci-dessus (≈6/jour, ≥15 min d'écart, ≥6 h par nœud) sont volontairement prudents.
