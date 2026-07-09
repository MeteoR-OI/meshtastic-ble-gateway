# Runbook MHA — Test du correctif `supervision_timeout` BLE

**Cible :** Raspberry Pi 3B+ « MHA235 » hébergeant `mbg.service` (passerelle BLE→MQTT).
**But :** mesurer le churn BLE actuel (baseline), appliquer le correctif
`supervision_timeout = 6 s` (réversible), vérifier qu'il est bien négocié sur le lien, puis
mesurer l'après. Diagnostic du 2026-07-09 : `supervision_timeout = 42` (420 ms) confirmé comme
déclencheur du churn 3-5 min sur lien faible (-80/-90 dBm).

## ⚠️ Règles impératives

- **Lecture seule, SAUF les 2 écritures debugfs explicitement autorisées** (Étapes 4 et 7),
  toutes deux **réversibles au reboot** (aucune persistance installée par ce runbook).
- **NE PAS** redémarrer, arrêter ou recharger `mbg.service` ni `bluetooth`.
- **NE PAS** toucher `/dev/ttyUSB0` (= datalogger weewx, RIEN à voir avec le node).
- **NE PAS** modifier la config du node Meshtastic (il est distant, en BLE).
- Renvoyer la **sortie brute** de chaque étape, étiquetée `### Étape N`. Coller les erreurs
  telles quelles (une commande qui échoue est une information).

---

## Étape 1 — Localiser la base `metrics.db`

```bash
# Le chemin dépend de MBG_DB_PATH (sinon relatif à WorkingDirectory=/opt/meshtastic-ble-gateway).
echo "== env du service =="
sudo systemctl show mbg -p Environment -p WorkingDirectory 2>&1
echo "== emplacements candidats =="
ls -l /opt/meshtastic-ble-gateway/metrics.db /var/lib/mbg/metrics.db 2>&1
```
→ Retiens le chemin réel de `metrics.db` (noté `<DB>` dans la suite). Utilise celui qui existe.

## Étape 2 — BASELINE : churn BLE depuis l'historique déjà enregistré

`link_quality` contient une ligne par reconnexion. La base est en WAL (lecture concurrente sûre
pendant que le service tourne).

```bash
DB=<DB>   # <-- remplace par le chemin trouvé à l'Étape 1
echo "== reconnexions sur la dernière heure =="
sudo sqlite3 "$DB" "SELECT COUNT(*) AS reconnexions_1h FROM link_quality WHERE ts >= strftime('%s','now')-3600;"
echo "== reconnexions/heure sur les 6 dernières heures =="
sudo sqlite3 -header -column "$DB" "
  SELECT CAST((strftime('%s','now')-ts)/3600 AS INT) AS h_ago, COUNT(*) AS reconnexions
  FROM link_quality WHERE ts >= strftime('%s','now')-21600
  GROUP BY h_ago ORDER BY h_ago;"
echo "== plage temporelle couverte par la table =="
sudo sqlite3 "$DB" "SELECT datetime(MIN(ts),'unixepoch','localtime'), datetime(MAX(ts),'unixepoch','localtime'), COUNT(*) FROM link_quality;"
```
→ **Note le taux baseline** (reconnexions/h). C'est le point de comparaison.

## Étape 3 — Relever la valeur ACTUELLE (avant changement)

```bash
sudo cat /sys/kernel/debug/bluetooth/hci0/supervision_timeout   # attendu : 42 (= 420 ms)
```

## Étape 4 — ✍️ APPLIQUER le correctif (écriture #1, réversible au reboot)

```bash
# 600 × 10 ms = 6 s (la valeur que le firmware Meshtastic ESP32 se fixe lui-même).
echo 600 | sudo tee /sys/kernel/debug/bluetooth/hci0/supervision_timeout
sudo cat /sys/kernel/debug/bluetooth/hci0/supervision_timeout   # doit afficher 600
```
→ La nouvelle valeur s'applique à la **prochaine reconnexion** (le node re-churn sous 3-5 min ;
inutile de forcer une déconnexion).

## Étape 5 — VÉRIFIER le timeout réellement négocié sur le lien (`btmon`)

Le point critique : BlueZ 5.55 en rôle *central* n'honore pas toujours la debugfs (bug #717). On
capture donc les paramètres réels de la prochaine (re)connexion.

```bash
# Capture ~7 min => couvre au moins un cycle de reconnexion (churn toutes les 3-5 min).
sudo timeout 420 btmon 2>&1 | grep -iE "Create Connection|Connection Complete|Connection Update|Supervision|Interval:|reason|Disconnect" 
```
→ Cherche une ligne **`Supervision timeout: 6000 ms`** (ou proche) associée à une
`LE Create Connection` / `Connection Complete`.
- **6000 ms** → ✅ le correctif est actif sur le lien.
- **toujours 420 ms** → ❌ BlueZ n'applique pas la debugfs : signale-le, on passera au plan B
  (`btmgmt`/mgmt). **N'installe rien de plus**, arrête là et rapporte.
- Note aussi tout `reason 0x08` (= supervision timeout) dans les `Disconnect` capturés.

## Étape 6 — (À relancer APRÈS ≥ 30-60 min) : churn APRÈS changement

Rejoue **exactement** la 1re commande de l'Étape 2 pour comparer sur une fenêtre postérieure au
changement :

```bash
DB=<DB>
echo "== reconnexions sur la dernière heure (fenêtre post-correctif) =="
sudo sqlite3 "$DB" "SELECT COUNT(*) AS reconnexions_1h FROM link_quality WHERE ts >= strftime('%s','now')-3600;"
```
→ Compare au baseline de l'Étape 2. Objectif : chute nette (ex. 15-20/h → quelques-unes/h).

## Étape 7 — (Optionnel) REVERT immédiat sans reboot

Uniquement si on veut annuler avant un reboot :

```bash
echo 42 | sudo tee /sys/kernel/debug/bluetooth/hci0/supervision_timeout
```

---

## Format du rapport attendu

```
### Étape 1 : <chemin DB réel + env>
### Étape 2 : baseline (reconnexions/h + tableau 6h + plage couverte)
### Étape 3 : supervision_timeout AVANT (attendu 42)
### Étape 4 : confirmation écriture (doit afficher 600)
### Étape 5 : timeout négocié vu par btmon (6000 ms ? 420 ms ?) + reasons de disconnect
### Étape 6 : (plus tard) reconnexions/h post-correctif
OBSERVATIONS : tout comportement anormal, erreur, ou écart.
```
