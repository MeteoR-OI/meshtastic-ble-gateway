# Runbook MHA — Plan B : `LE Connection Update` (hcitool lecup)

**Cible :** RPi 3B+ « MHA235 » hébergeant `mbg.service`.
**Contexte :** le test #1 a montré que BlueZ 5.55 en central **ignore** la debugfs
`supervision_timeout` (bug #717) ; la cause du churn est confirmée = `Disconnect reason 0x08`
(supervision timeout, 420 ms). **But de ce runbook :** prouver que forcer le supervision timeout à
**6 s sur le lien vivant** via `hcitool lecup` (HCI LE Connection Update, initiée par le central)
est honoré, puis mesurer la baisse du churn sur ~40 min. Baseline connue : **~18-27 reconnexions/h**.

## ⚠️ Règles impératives

- **Lecture seule, SAUF les commandes `hcitool lecup` (Étapes 3 et 4)** = modifications du lien
  **volatiles** (n'existent que pour la durée de la connexion ; aucune persistance installée).
- **NE PAS** redémarrer/arrêter/recharger `mbg.service` ni `bluetooth`.
- **NE PAS** toucher `/dev/ttyUSB0` (datalogger weewx). **NE PAS** modifier le node.
- Tout se lance en root (sudo). Renvoyer la **sortie brute** étiquetée `### Étape N`, erreurs comprises.
- `hcitool` est déprécié mais présent sur BlueZ 5.55 — c'est l'outil pragmatique pour émettre une
  LE Connection Update. Il coexiste avec `bluetoothd` (déjà vérifié : `hcitool con` fonctionne).

---

## Étape 1 — Identifier la connexion BLE du node (MAC + handle courant)

```bash
echo "== connexions LE actives =="
sudo hcitool con
echo "== extraction MAC + handle du node =="
NODE=$(sudo hcitool con | grep -oE '([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}' | head -1)
H=$(sudo hcitool con | awk '/handle/{for(i=1;i<=NF;i++) if($i=="handle"){print $(i+1); exit}}')
echo "NODE=$NODE  HANDLE=$H"
```
→ Si `NODE`/`H` sont vides, le node est déconnecté à l'instant T ; relance après quelques secondes
(il se reconnecte sous 2-3 min).

## Étape 2 — Démarrer une capture `btmon` (pour prouver le timeout négocié)

```bash
sudo sh -c 'timeout 90 btmon > /tmp/btmon_lecup.txt 2>&1 &'
sleep 2
echo "btmon capture -> /tmp/btmon_lecup.txt (90 s)"
```

## Étape 3 — ✍️ Forcer le supervision timeout à 6 s sur le lien vivant

```bash
# Recalcule NODE/H au cas où le handle aurait changé depuis l'Étape 1.
NODE=$(sudo hcitool con | grep -oE '([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}' | head -1)
H=$(sudo hcitool con | awk '/handle/{for(i=1;i<=NF;i++) if($i=="handle"){print $(i+1); exit}}')
echo "Application lecup sur NODE=$NODE HANDLE=$H"
# --min/--max en unités 1.25 ms (24/40 = 30/50 ms, inchangé) ; --timeout en unités 10 ms (600 = 6 s)
sudo hcitool lecup --handle "$H" --min 24 --max 40 --latency 0 --timeout 600
echo "exit=$?"
sleep 3
echo "== ce que btmon a vu (doit montrer Supervision timeout: 6000 msec) =="
grep -iE -A8 "Connection Update" /tmp/btmon_lecup.txt | grep -iE "Connection Update|Status|Supervision|interval"
```
→ Cherche **`LE Connection Update Complete → Status: Success → Supervision timeout: 6000 msec`**.
- **6000 msec** → ✅ le plan B fonctionne, passe à l'Étape 4.
- **erreur `lecup` / handle invalide** → le lien a peut-être coupé pile à ce moment ; relance
  l'Étape 3. Si `lecup` échoue systématiquement (option non supportée), colle l'erreur et arrête.

## Étape 4 — Boucle « watch-and-apply » pendant 40 min (mesure du bénéfice)

Ré-applique `lecup` à **chaque nouvelle connexion** (nouveau handle) et logue l'horodatage. Le
nombre de lignes `lecup OK` = nombre de reconnexions pendant la fenêtre → comparaison directe au
baseline.

```bash
sudo bash -c '
NODE=$(hcitool con | grep -oE "([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}" | head -1)
echo "Surveillance de $NODE pendant 40 min…"
LAST=""; END=$(( $(date +%s) + 2400 ))
while [ $(date +%s) -lt $END ]; do
  H=$(hcitool con | awk "/handle/{for(i=1;i<=NF;i++) if(\$i==\"handle\"){print \$(i+1); exit}}")
  if [ -n "$H" ] && [ "$H" != "$LAST" ]; then
    if hcitool lecup --handle "$H" --min 24 --max 40 --latency 0 --timeout 600 2>/dev/null; then
      echo "$(date "+%H:%M:%S") lecup OK handle=$H"; LAST="$H"
    fi
  fi
  sleep 3
done
echo "fin fenetre de test"
'
```
→ Compte les lignes `lecup OK` : baseline = **~12-18 reconnexions attendues sur 40 min** (18-27/h).
Nettement moins = le plan B stabilise le lien.

## Étape 5 — Confirmer via `link_quality` (mesure indépendante)

```bash
DB=/var/lib/mbg/metrics.db
echo "== reconnexions sur les 40 dernières min (fenêtre de test) =="
sudo sqlite3 "$DB" "SELECT COUNT(*) AS reco_40min FROM link_quality WHERE ts >= strftime('%s','now')-2400;"
echo "== rappel baseline : reconnexions/h sur 6h avant test =="
sudo sqlite3 -header -column "$DB" "
  SELECT CAST((strftime('%s','now')-ts)/3600 AS INT) AS h_ago, COUNT(*) AS reco
  FROM link_quality WHERE ts >= strftime('%s','now')-21600 GROUP BY h_ago ORDER BY h_ago;"
```

## Étape 6 — Nettoyage (aucune persistance)

```bash
# Rien à annuler : lecup est volatile (disparaît au prochain drop). btmon s'est déjà arrêté (timeout).
sudo pkill -f "btmon" 2>/dev/null; true
rm -f /tmp/btmon_lecup.txt
echo "nettoyage OK"
```

---

## Format du rapport attendu

```
### Étape 1 : NODE + HANDLE
### Étape 3 : sortie lecup + extrait btmon (Supervision timeout: 6000 msec ? erreur ?)
### Étape 4 : liste des lignes "lecup OK" (=> nb de reconnexions sur 40 min)
### Étape 5 : reco_40min + tableau baseline 6h
CONCLUSION : le plan B tient-il le 6000 ms ? le churn baisse-t-il vs baseline (18-27/h) ?
OBSERVATIONS : erreurs, comportements anormaux, drops résiduels et leur reason.
```
