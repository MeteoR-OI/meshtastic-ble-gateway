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
