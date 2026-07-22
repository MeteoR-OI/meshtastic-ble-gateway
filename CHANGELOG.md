# Changelog

Toutes les évolutions notables. Format inspiré de [Keep a Changelog](https://keepachangelog.com/fr/) ;
versionnage [SemVer](https://semver.org/lang/fr/). Notes et artefacts détaillés :
[Releases GitHub](https://github.com/MeteoR-OI/meshtastic-ble-gateway/releases).

## [0.9.4] — 2026-07-22
### Modifié
- **Identité syslog stable** : l'unité `deploy/mbg.service` pose `SyslogIdentifier=meteor-mbg`, si
  bien que le service journalise sous **`app_name:meteor-mbg`** (et non le générique `python` du
  basename de l'interpréteur) — distinct des autres services Python de la station dans un agrégateur
  type VictoriaLogs. **Source d'identité unique** : aucun `openlog()`/`SysLogHandler` ajouté côté
  code. Gardé par `tests/test_deploy_service.py`. Chantier inter-repos `log-identity`.

## [0.9.3] — 2026-07-18
### Ajouté
- **Histogramme « paquets reçus par nombre de sauts, par tranche »** : nouvel endpoint
  **`GET /hops`** (`?since=<epoch_s>&bin=<sec>`, `bin` ∈ [60, 86400], défaut 300, réfléchi dans la
  réponse), **frère** de `/packets` sur la dimension « saut ». Renvoie `{bin, rows}` — `rows` =
  `[bin_start, hops, count]` triées par `bin_start`, une tranche sans paquet pour un `hops` n'ayant
  **pas** de ligne (remplissage à `0` = charge du consommateur). `hops` ∈ **{0..7}** (0 = Direct),
  **`-1` = Inconnu** (paquet **distant** au saut indéterminé) ou **`-2` = Local** (émis par la
  passerelle) ; **pas de map de noms** (la dimension est un entier fixe). **Même
  population que `/packets`** (nœud local compris) : la somme des `count` d'une tranche sur tous les
  `hops` égale le total `/packets` de la tranche (invariant). **Nécessite le monitoring** (sinon
  `404 monitoring désactivé`, comme `/metrics` et `/packets`) ; **authentifié** comme toutes les
  routes. Chaque paquet entrant est compté dans le **même** chemin radio et **sous le même verrou**
  que `/packets` (un second `dict[hops] += 1`, sans I/O, sans jamais lever), vidé aux mêmes moments
  (cadence du monitoring **et** décrochage du lien). Agrégation **en SQL** (`GROUP BY b, hops` sur
  `idx_packet_hops_ts`), jamais en Python ; cardinalité **bornée à ≤ 10 buckets** (indépendante de la
  taille du mesh). Nouvelle table `packet_hops` (série temporelle), partageant le **plafond dur de
  rétention 35 j** de `packet_counts` (même `prune_packets`, purge inconditionnelle même quand
  `MBG_RETENTION_DAYS=0`) et exportée en CSV. Cf.
  [monitoring.md](docs/monitoring.md#paquets-reçus-par-nombre-de-sauts-get-hops).
- **Calcul du saut** : le bucket **`-2` (Local) est prioritaire** — si l'émetteur est le nœud
  passerelle (même dérivation `fromId`/`from` que le compteur par nœud ; id local lu du cache
  NodeDB via `getMyNodeInfo`, sans I/O BLE), le paquet va en `-2` **avant** tout calcul de saut
  (finding live PAM289 : ~92 % des paquets locaux n'ont pas de `hopStart`, ils noyaient l'Inconnu
  `-1` qui doit désigner un paquet **distant**). Sinon `hopStart`/`hopLimit` sont des clés
  **top-level camelCase** du dict décodé (sœurs de `fromId`) : `hops = hopStart − hopLimit` si
  `hopStart` est un int ≥ 1 ; **un `hopLimit` manquant vaut `0`** (et non Inconnu) car
  `MessageToDict` omet les champs à zéro — un paquet multi-hop ayant épuisé son budget arrive avec
  `hopLimit == 0` (clé absente). Seul `hopStart` absent/`0`/non-int (firmware ancien) → bucket
  **`-1` (Inconnu)**, tout comme un `hops` calculé hors [0..7]. Ne lève **jamais** (chemin radio) ;
  l'invariant Σ count(hops, tous buckets dont `-1` et `-2`) == total `/packets` reste vrai.

## [0.9.2] — 2026-07-16
### Ajouté
- **Histogramme « paquets reçus par nœud, par tranche »** : nouvel endpoint **`GET /packets`**
  (`?since=<epoch_s>&bin=<sec>`, `bin` ∈ [60, 86400], défaut 300, réfléchi dans la réponse) qui
  renvoie `{bin, nodes, rows}` — `rows` = `[bin_start, node_id, count]` triées par `bin_start`,
  une tranche sans paquet n'ayant **pas** de ligne (le remplissage à `0` est la charge du
  consommateur) ; `nodes` = `node_id → nom affichable` (`short_name || long_name || node_id`,
  jamais `null`), limité aux nœuds présents dans `rows`. **Nécessite le monitoring** (sinon `404
  monitoring désactivé`, comme `/metrics`) ; **authentifié** comme toutes les routes.
  Chaque paquet entrant est compté **avant** le filtre de portnum (tous portnums confondus) dans
  `_handler_receive`, sous verrou dédié, **sans I/O et sans jamais lever** (chemin radio) ; un
  paquet sans émetteur exploitable n'est pas compté (pas de nœud fantôme `!00000000`). Les
  compteurs sont vidés en base à la cadence du monitoring **et au décrochage du lien** — sans ce
  second flush, toute session plus courte que `MBG_MONITOR_INTERVAL` perdrait ses comptages (même
  classe de bug que le `node_metrics` vide de 2026-07-08). L'**agrégation est faite en SQL**
  (`GROUP BY` sur `idx_packets_ts`), jamais en Python : ~4 800 lignes servies au lieu de ~170 k sur
  une fenêtre mois. Deux tables neuves : `packet_counts` (série temporelle) et `node_names`
  (identité dédiée — `neighbor_registry` ne convient pas : il ne contient que les voisins actifs à
  `hopsAway` connu, donc n'est pas un sur-ensemble des nœuds comptés). Cf.
  [monitoring.md](docs/monitoring.md#paquets-reçus-par-nœud-get-packets).
- **Rétention propre à `packet_counts` : plafond dur de 35 jours**, appliqué **même quand
  `MBG_RETENTION_DAYS=0`** (le défaut = « ne rien purger ») — une série temporelle qui ne se purge
  jamais est une fuite lente (~5 800 lignes/jour, ~200 k lignes à l'équilibre). `prune_packets()`
  ne touche **que** `packet_counts` : les autres tables restent gouvernées par
  `MBG_RETENTION_DAYS` seul (aucune purge par effet de bord pour une station qui a choisi de tout
  garder). `MBG_RETENTION_DAYS` inférieur à 35 j s'applique aussi aux paquets : 35 j est un
  plafond, pas un plancher. Le thread de maintenance démarre désormais **dès qu'un store existe** —
  auparavant il ne démarrait pas sans `MBG_DUMP_DIR` ni `MBG_RETENTION_DAYS`, donc aucune purge
  n'aurait tourné en configuration par défaut.

### Documentation
- **Toutes les routes de l'API exigent le token, `GET` compris** (`/health`, `/info`, `/metrics`,
  `/history`, `/packets` → `401` sans en-tête) : `handle_request` vérifie l'autorisation **avant**
  de dispatcher la méthode, et l'API n'existe que si `MBG_API_TOKEN` est défini. Le point n'était
  pas documenté et se croyait volontiers « POST-only ». Piège de test associé, désormais consigné :
  une assertion d'auth écrite avec un token **vide** passe à vide (`hmac.compare_digest("", "")`
  est vrai) — toute assertion d'authentification doit utiliser un token **non vide**. Cf.
  [api.md](docs/api.md#activation--sécurité).

## [0.9.1] — 2026-07-14
### Corrigé
- **Migration `node_metrics` manquante → crash-loop sur bases pré-existantes.** Les colonnes
  `node_id` / `node_name` (ajoutées à `_SCHEMA` en v0.7) n'étaient **pas** dans `_MIGRATIONS` : sur une
  base créée avant v0.7 (0.3/0.4/0.6.x — ex. **CHAR645**, MHA235), `CREATE TABLE IF NOT EXISTS` ne les
  ajoute jamais → `record_node` échoue (`table node_metrics has no column named node_id`) → **worker en
  crash-loop (~5 s)**. Effet de bord grave : à chaque respawn le planificateur re-tire un traceroute
  (le min-gap ne « voit » pas les envois d'un worker qui crashe avant la persistance du résultat) →
  **inondation traceroute du mesh**. Les deux colonnes sont désormais migrées à l'init (`ALTER TABLE`,
  idempotent, non destructif) → toute base existante se répare **seule au démarrage**, sans écriture
  manuelle. Audit effectué : aucune autre table de `_SCHEMA` ne présentait cet écart (tables
  `neighbor_registry`/`traceroute` créées à neuf, `link_quality` inchangée).

## [0.9.0] — 2026-07-14
### Ajouté
- **Traceroute** : endpoint **`POST /traceroute`** (async `202`, ou `wait:true` bloquant) qui trace
  la route mesh vers un node — chemin **aller** (`[passerelle, *relais, dest]`) et **retour** si le
  firmware distant le renseigne, avec le **SNR par saut** (firmware/4, sentinelle `-128` → `null`).
  Émission + corrélation **dans le worker qui tient déjà le lien BLE** → aucune coupure, **pas de 2ᵉ
  connexion BLE**. La réponse arrive dans la boucle de réception existante, corrélée par
  `requestId` + `from` ; timeout de repli. Résultat **publié en MQTT** (`MBG_TRACEROUTE_TOPIC`) +
  **écrit en SQLite** (`GET /history?type=traceroute`) + compteurs dans `GET /metrics`
  (`traceroute_sent/ok/timeout/error_total`, `traceroute_last_rtt_ms`). Le mode `wait:true` relit la
  ligne SQLite (base WAL) côté API — il **ne bloque jamais** la boucle du worker. Cf.
  [docs/traceroute.md](docs/traceroute.md).
- **Planificateur de traceroute automatiques** — **opt-in `MBG_TRACEROUTE_ENABLED`** (désactivé par
  défaut). Politiques enfichables **`static`** (round-robin sur `MBG_TRACEROUTE_TARGETS`) et
  **`staleness`** (défaut : le node entendu récemment au dernier traceroute réussi le plus ancien ;
  jamais-tracé = priorité max). Garde-fous airtime : budget quotidien, intervalle min global (+
  jitter), min par nœud, heures calmes, garde d'occupation canal, BLE down. État persistant en SQLite
  (survit aux restarts). Variables `MBG_TRACEROUTE_*` documentées + exemples de drop-in par mode.
- **Réconciliation BLE pré-spawn** — **opt-in `MBG_BLE_RECONCILE`**. Avant chaque spawn de worker, le
  superviseur libère le node resté `Connected` dans bluez (ACL résiduel d'un worker SIGKILL, ou stop
  mal fermé) → le node ré-émet ses advertisements → **reconnexion fiable au restart/respawn** (fin de
  la « danse » de scans qui gèlent `connect_grace` s). **N'appaire/désappaire jamais** (réutilise le
  node appairé) ; WARNING si `Trusted=yes` (auto-reconnexion bluez) ou présent-non-appairé.
  `MBG_BLE_SETTLE` = délai de ré-émission après disconnect (défaut 3 s).

### Note d'exploitation
- Le topic MQTT par défaut **`mbg/traceroute` est rejeté par un broker MeshForge** (ACL du compte
  station publish-only, scopée `msh/#` — vérifié sur `mqtt-mt.meteor-oi.re`). Surcharger
  `MBG_TRACEROUTE_TOPIC` vers `msh/<région>/mbg/traceroute/!<nodeid>` (sans segment `/2/`, pour ne pas
  être ingéré par MeshForge). Le rejet d'un publish QoS 0 est silencieux ; le résultat reste en
  SQLite / `/history`.

## [0.8.2] — 2026-07-12
### Corrigé
- **Voisins actifs** : les métriques de voisinage (`count`, `best_snr`, `max_distance_km`,
  `distinct_*`) balayaient toute la NodeDB — y compris des nodes entendus il y a longtemps, dont la
  **position périmée gonflait `max_distance_km`**. Le voisinage ne compte plus que les voisins
  **actifs** (entendus depuis `max(MBG_MONITOR_INTERVAL, 3600 s)`, surchargeable par
  **`MBG_NEIGHBOR_ACTIVE_SECS`**). Un voisin sans `last_heard` est exclu (fraîcheur non prouvable).

### Ajouté (PORTÉE v2)
- **Registre NodeDB persistant** (`neighbor_registry` dans `metrics.db`, une ligne par node,
  mergé avec la NodeDB live à chaque sonde) : toutes les métriques voisinage se calculent dessus →
  elles **survivent aux reconnexions** (fini le sous-comptage post-restart où `count` restait
  bloqué). Le registre n'est pas purgé (`distinct_total` = tout l'historique) ; il vieillit par le
  filtre d'activité. Au 1er démarrage, il est **graine** depuis l'ancienne table snapshot `neighbors`
  (si présente) pour préserver la continuité de `distinct_total`. L'ancienne table n'est plus utilisée
  ensuite (orpheline dans les bases existantes).
- **2ᵉ distance multi-hop** : `GET /metrics.neighbors` expose `max_distance_hops_km` (voisin relayé
  `hops_away ≥ 1` le plus lointain) en plus de `max_distance_km` (direct, `hops_away == 0`).
  `null` si la catégorie est vide ; **`0.0` km est valide** (nodes co-localisés).

## [0.8.1] — 2026-07-12
### Ajouté
- **Portée & voisinage** dans `GET /metrics` (bloc `neighbors`) — calcul/SQL sur des données
  DÉJÀ remontées, **aucune nouvelle op BLE** :
  - `max_distance_km` : distance (haversine, km arrondi 0,1) du voisin 0-hop le plus lointain dont
    on connaît la position ; calculée par la sonde, persistée (colonne `node_metrics.max_distance_km`,
    migration auto) ; `null` si la passerelle ou tous les voisins n'ont pas de position.
  - `distinct_1h` / `distinct_24h` / `distinct_total` : voisins distincts (`COUNT(DISTINCT node_id)`
    sur la table `neighbors`) sur 1 h / 24 h / tout l'historique.

## [0.8.0] — 2026-07-11
### Ajouté
- **Outil de provisionnement** `python -m mbg.provision` (`--inspect`/`--apply`) : lit/écrit la
  config MQTT + position du node par BLE (une seule transaction, retry BLE, gestion du reboot
  post-commit avec budget de reconnexion patient et **exit 2 = commité-mais-non-vérifié** ;
  sortie dure via `os._exit` pour ne jamais geler sur les threads non-daemon de bleak),
  sortie JSON stable pour l'installateur — voir [docs/provision.md](docs/provision.md).
- **Statut d'onboarding dans `GET /info`** : `broker`, `mqtt_proxy_ok`, `map_reporting`, lus de la
  config MQTT du node par la sonde (colonnes `node_metrics.mqtt_*`, migration auto des bases
  existantes) — consommés par l'intégration WeeWX.

### Corrigé
- `pyproject.toml` réaligné sur la version `0.7.0` (resté à `0.6.1` lors du tag).

## [0.7.0] — 2026-07-10
### Ajouté
- **Identité du node local** dans le monitoring : `getMyNodeInfo()['user']` (id + nom humain)
  persisté en base (`node_metrics.node_id`/`node_name`) et exposé via `/metrics.node`.
- **Agrégat voisins** dans `GET /metrics` : `neighbors: {count, best_snr}` (dernier batch).
- **`GET /info`** (derrière token) : `{version, node_id, node_name, monitor_interval,
  battery_tiers}` — surface de découverte pour l'intégration WeeWX et la tuile de l'installateur.

Base de l'**intégration WeeWX** (extension `weewx-mbg`, skin, installateur — repos dédiés).

## [0.6.1] — 2026-07-09
### Corrigé
- **Fin du churn BLE** : le superviseur force un `bluetoothctl disconnect` (borné) après chaque
  SIGKILL d'un worker gelé — sinon `bluetoothd` gardait l'ACL, le node cessait d'émettre et le
  respawn ne le retrouvait pas (boucle `No peripheral found`). Reconnexion désormais automatique.

## [0.6.0] — 2026-07-09
### Ajouté
- **Requêtes vers un node distant** : `POST /send/telemetry` avec `dest` et `POST /request/position`
  (`wantResponse`) — la réponse du node distant remonte en `[uplink]` MQTT.
- **Support Raspberry Pi OS Buster** : Python 3.11 isolé (`/opt`), **BlueZ 5.55 vendorisé**
  (`bluez-meshforge`), pin `bleak==1.1.1` (`constraints.txt`), **artefacts pré-compilés** (Python
  `.deb` + wheelhouse armhf) pour une install hors-ligne sans compilation.

## [0.5.0] — 2026-07-09
### Ajouté
- **Stabilisation du lien BLE sur signal faible** (opt-in) : impose le supervision timeout au lien
  vivant via `hcitool lecup`, une fois par session — churn réduit d'~94 % en terrain.

## [0.4.0] — 2026-07-09
### Ajouté
- **Paliers batterie + duty-cycle** (opt-in) : cadence adaptative (15/30/60 min) selon la batterie
  du node, duty-cycle < 25 % (lien coupé pour laisser le node dormir), hystérésis anti-flapping,
  télémétrie diffusée au changement de mode.

## [0.3.0] — 2026-07-09
### Ajouté
- **Monitoring / sonde** : métriques node (batterie fraîche via lecture active) + qualité du lien
  BLE en SQLite (WAL), API `GET /metrics` & `/history`, export CSV + purge.
- `POST /send/position` : ré-émet la position fixe du node (jamais `0,0`).

## [0.2.0] — 2026-07-08
### Ajouté
- **API de contrôle / downlink** (opt-in, token) : `/send/text` (avec `want_ack`), `/send/telemetry`,
  `/admin`, `/health`. ACK radio corrélé via `meshtastic.receive` (contourne un bug BLE 2.7.10).

## [0.1.0] — 2026-07-07
### Ajouté
- **Passerelle durcie** : forward opaque du Client Proxy `/e/`, **résilience par isolation de
  process** (superviseur + worker jetable SIGKILLable), watchdog systemd, backoff de reconnexion.
- 100 % branch coverage, CI, Docker, service systemd. Plancher Python 3.9.

[0.6.1]: https://github.com/MeteoR-OI/meshtastic-ble-gateway/releases/tag/v0.6.1
[0.6.0]: https://github.com/MeteoR-OI/meshtastic-ble-gateway/releases/tag/v0.6.0
[0.5.0]: https://github.com/MeteoR-OI/meshtastic-ble-gateway/releases/tag/v0.5.0
[0.4.0]: https://github.com/MeteoR-OI/meshtastic-ble-gateway/releases/tag/v0.4.0
[0.3.0]: https://github.com/MeteoR-OI/meshtastic-ble-gateway/releases/tag/v0.3.0
[0.2.0]: https://github.com/MeteoR-OI/meshtastic-ble-gateway/releases/tag/v0.2.0
[0.1.0]: https://github.com/MeteoR-OI/meshtastic-ble-gateway/releases/tag/v0.1.0
