# Monitoring / sonde

Activée si **`MBG_MONITOR_INTERVAL > 0`**, la sonde historise en **SQLite local** (stdlib, zéro
dépendance, mode WAL) les métriques du node **et** la qualité du lien BLE — base des
[paliers batterie](battery-tiers.md).

Point clé : **lecture batterie ACTIVE** locale (`getMyNodeInfo`), qui **contourne le broadcast
télémétrie de 12 h** du node → mesure toujours fraîche. Un relevé est fait **tôt dans chaque
session** puis à la cadence `MBG_MONITOR_INTERVAL` (robuste au churn : sur lien instable, les
sessions peuvent être plus courtes que la cadence). Si les [paliers batterie](battery-tiers.md)
sont actifs, la cadence suit **le palier** (15/30/60 min) et non `MBG_MONITOR_INTERVAL`.

## Métriques collectées

| Table | Écrivain | Contenu |
|---|---|---|
| `node_metrics` | worker | **identité** (node_id, node_name), batterie, voltage, utilisation canal/air, uptime, position (lat/lon/alt) |
| `neighbors` | worker | voisins directs (0-hop) : node_id, SNR, RSSI **radio**, last_heard |
| `link_quality` | superviseur | **compteur de reconnexions** = signal de qualité du lien BLE |
| `packet_counts` | worker | **paquets reçus par nœud** (série temporelle, une ligne par nœud et par flush) |
| `node_names` | worker | noms affichables (`short_name`/`long_name`) de la NodeDB — nomme `packet_counts` |

`GET /metrics` renvoie `{node, link, neighbors}` : `node` (dernier relevé, avec `node_id`/`node_name`),
`link` (reconnexions), et un **agrégat voisins** `neighbors: {count, best_snr}` (dernier batch).

`MBG_MONITOR_FORCE_TELEMETRY=true` force un `sendTelemetry` avant chaque relevé si le firmware ne
rafraîchit pas passivement (coûte de l'airtime).

> **Pas de RSSI absolu du lien BLE** : sur BlueZ, `bluetoothd` détient le contrôleur, donc le RSSI
> d'un lien LE connecté n'est plus exposé (ni `hcitool rssi`, ni `btmgmt conn-info`, ni D-Bus
> `Device1.RSSI`, même en root) — vérifié terrain. Le **compteur de reconnexions** (`link_quality`)
> est le signal de qualité BLE. Voir [troubleshooting.md](troubleshooting.md).

## Accès

**API** (même token que le contrôle) :

```bash
curl -H "X-API-Token: $TOKEN" $BASE/metrics                        # dernier relevé {node, link}
curl -H "X-API-Token: $TOKEN" "$BASE/history?since=0&limit=100"    # série node_metrics
```

**SQLite direct** (scripts locaux) — la base est lisible pendant que le service tourne (mode WAL) :

```bash
sqlite3 /var/lib/mbg/metrics.db \
  "SELECT datetime(ts,'unixepoch','localtime'), battery_level, voltage FROM node_metrics ORDER BY ts DESC LIMIT 5;"
```

**Export CSV** périodique (`MBG_DUMP_DIR`, cadence `MBG_DUMP_INTERVAL`) + purge optionnelle
(`MBG_RETENTION_DAYS`). Créer le répertoire avec les bons droits :

```bash
sudo install -d -o mbg -g mbg /var/lib/mbg   # ex. MBG_DB_PATH=/var/lib/mbg/metrics.db, MBG_DUMP_DIR=/var/lib/mbg/csv
```

Variables : [configuration.md](configuration.md#monitoring--sonde-opt-in).

## Paquets reçus par nœud (`GET /packets`)

Histogramme **« paquets reçus par nœud, par tranche »** — la donnée qui alimente le chart stacké
d'une page Monitoring. Chaque paquet entrant est compté **avant** tout filtre de portnum (tous
portnums confondus), dans le chemin radio : un `dict[node_id] += 1` sous verrou, sans I/O, qui ne
lève jamais. Les compteurs sont vidés en base à la cadence du monitoring **et au décrochage du
lien** (sans quoi une session plus courte que `MBG_MONITOR_INTERVAL` perdrait tous ses comptages).

Nécessite le monitoring (`MBG_MONITOR_INTERVAL > 0`) — sinon `404 {"ok": false, "error":
"monitoring désactivé"}`, comme `/metrics`. Aucune variable dédiée, aucune option à activer.

```bash
curl -H "X-API-Token: $TOKEN" "$BASE/packets?since=$(( $(date +%s) - 86400 ))&bin=900"
```

```json
{
  "bin": 900,
  "nodes": {"!a4f2c1b0": "Piton Maïdo", "!1f30c7d2": "Relais Tampon"},
  "rows": [[1783622100, "!a4f2c1b0", 42], [1783622100, "!1f30c7d2", 7]]
}
```

| Paramètre | Défaut | Rôle |
|---|---|---|
| `since` | `0` | epoch (s) : ne renvoie que les tranches à partir de cet instant |
| `bin` | `300` | largeur de tranche (s), bornée **[60, 86400]** ; **réfléchie** dans la réponse |

- `rows` = `[bin_start_epoch_s, node_id, count]`, **triées par `bin_start` croissant**, avec
  `bin_start = floor(ts / bin) * bin`.
- Une tranche **sans paquet** pour un nœud **n'a pas de ligne** : le remplissage à `0` est la
  charge du consommateur (un stack honnête le fait chez lui, pas au fil de l'eau).
- `nodes` = `node_id → nom affichable`, résolu `short_name || long_name || node_id`. Ne contient
  que les nœuds présents dans `rows` ; un nœud jamais nommé par la sonde apparaît **quand même**,
  sous son `node_id` — jamais absent, jamais `null`.
- **Le nœud local (la passerelle) est compté comme les autres** : *il émet, donc il compte*.
  ⚠️ **Asymétrie assumée** avec l'agrégat `neighbors` de `/metrics`, qui **exclut** le nœud local :
  `/packets` montre donc **N+1 émetteurs** là où `neighbors.count` en voit **N**. Ce n'est pas une
  incohérence à corriger — « voisins » répond à *qui est autour de moi*, l'histogramme à *qui
  émet*. Un nœud local bavard se retire côté affichage (légende cliquable).
- `400 {"ok": false, "error": "paramètres invalides"}` si `since`/`bin` ne sont pas numériques ou
  si `bin` sort des bornes.

**L'agrégation est faite en SQL** (`GROUP BY` sur `idx_packets_ts`), jamais en Python : le client
ne reçoit jamais de lignes brutes — sans re-binning SQL, une fenêtre mois en transférerait ~170 k.

**Perf mesurée sur Raspberry Pi (ARM, PAM289)**, base saturée à 35 j × 288 tranches/jour :

| Nœuds | Lignes | `.db` | `day` (24 h, `bin=900`) | `month` (30 j, `bin=10800`) |
|---|---|---|---|---|
| **6** (mesh réel de la station) | 60 480 | 2,8 Mo | **11 ms** | **275 ms** |
| 12 | 120 960 | 5,7 Mo | 22 ms | 579 ms |
| 20 | 201 600 | 9,6 Mo | 38 ms | 1 003 ms |

La montée est **linéaire** en nombre de nœuds. `day` (l'appel de régime, à chaque cycle de report)
reste **deux ordres de grandeur** sous sa cible de 300 ms. `month` ne tourne qu'**1×/jour** et
n'atteint la seconde que vers **~20 nœuds** — c'est le seuil à surveiller si le mesh grossit ; le
recours connu est d'abaisser le Top-N ou de le porter dans le SQL.

> **Index couvrant : testé et rejeté** — un `idx(ts, node_id, count)` ne gagne que ~7 % (999 →
> 927 ms à 20 nœuds) pour **+60 % de disque** (9,6 → 15,4 Mo). Le plan bascule bien en *covering
> index*, mais le coût est le `GROUP BY` + tri, pas la lecture des lignes. `idx_packets_ts` seul
> est le bon compromis : ne pas « optimiser » ce point.

### Rétention : plafond dur de 35 jours

`packet_counts` est la seule table à **plafond de rétention propre : 35 jours**, appliqué
**inconditionnellement**, même quand `MBG_RETENTION_DAYS=0` (le défaut, qui signifie « ne rien
purger »). C'est délibéré : une série temporelle qui ne se purge jamais est une fuite lente
(~5 800 lignes/jour), alors que 35 j couvrent la fenêtre du chart « mois » avec de la marge. Les
**autres** tables restent gouvernées par `MBG_RETENTION_DAYS` seul — une station qui a choisi `0`
garde intégralement son historique `node_metrics`/`link_quality`/`traceroute`. Si
`MBG_RETENTION_DAYS` est fixé **en deçà** de 35 j, `packet_counts` suit cette valeur, plus courte :
35 j est un **plafond**, pas un plancher. La purge tourne dans le thread de maintenance, à la
cadence `MBG_DUMP_INTERVAL` (défaut 1 h).

`node_names` n'est **jamais** purgée (une ligne par nœud, bornée par la taille du mesh) : un nom
doit survivre aussi longtemps que les comptages qu'il nomme.

> **Perte assumée** : un worker **figé puis SIGKILL** par le superviseur emporte ses compteurs
> encore en RAM (au plus un `MBG_MONITOR_INTERVAL`). C'est le prix de l'isolation de process
> (`os._exit`/SIGKILL), qui est le socle de la résilience — voir [resilience.md](resilience.md).
> Le décrochage BLE *ordinaire*, lui, est couvert : la session vide les compteurs avant de rendre
> la main.
