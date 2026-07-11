# Provisionnement du node (`python -m mbg.provision`)

Outil CLI qui lit ou écrit la **config MQTT + position** d'un node Meshtastic par BLE, pour
l'onboarding d'une station (appelé par l'installateur ; utilisable à la main). Il produit
**un objet JSON sur stdout** (interface stable ; les logs vont sur stderr).

> **Prérequis** : le node est **déjà appairé** au système (l'appairage OS n'est PAS du ressort
> de cet outil) et la passerelle **mbg est arrêtée** (BLE = 1 seul client à la fois) :
> `sudo systemctl stop mbg`, provisionner, puis `sudo systemctl start mbg`.

## Usage

```bash
python -m mbg.provision --mac <MAC> --inspect                 # lecture seule
python -m mbg.provision --mac <MAC> --apply \                 # écrit la cible + relit
    [--broker H] [--port P] [--username U] [--password W] \
    [--root T] [--precision N] [--publish-interval S] \
    [--consent true|false] [--fixed-position] [--broadcast-secs S]
```

| Option | Défaut | Rôle |
|---|---|---|
| `--mac` | (requis) | adresse BLE du node |
| `--inspect` / `--apply` | (requis, exclusifs) | lire seulement / écrire la cible puis vérifier |
| `--broker` | `mqtt-mt.meteor-oi.re` | broker MQTT cible (`mqtt.address` du node) |
| `--port` | `1883` | port ; ≠ 1883 → `address` = `host:port` |
| `--username` / `--password` | (absents) | creds MQTT ; **absents = ceux du node sont conservés** |
| `--root` | `msh/EU_868` | topic racine MQTT |
| `--precision` | `15` | `position_precision` du map report (**15 ≈ 729 m**) |
| `--publish-interval` | `3600` | `publish_interval_secs` du map report |
| `--consent` | `true` | `should_report_location` (consentement carte) |
| `--fixed-position` | (off) | active `fixed_position` (absent = non modifié) |
| `--broadcast-secs` | `900` | `position_broadcast_secs` |

## Ce que `--apply` écrit (une seule transaction)

`moduleConfig.mqtt` : `enabled`, `proxy_to_client_enabled`, `encryption_enabled`, `json_enabled`
à vrai, `tls_enabled` à faux, `map_reporting_enabled` + `map_report_settings` (intervalle,
précision, consentement), `address`/`root`/creds — plus `uplink_enabled` sur le canal primaire
et `localConfig.position` (`position_broadcast_secs`, `fixed_position` si demandé). Les
écritures sont regroupées en **une transaction** (`beginSettingsTransaction` → `writeConfig` →
`commitSettingsTransaction`) pour ne provoquer qu'**un seul reboot** ; seules les sections
réellement modifiées sont écrites, et **si le node est déjà conforme, rien n'est écrit**
(zéro reboot).

## Sortie JSON (contrat stable)

```json
{ "node_id": "!534bbea5", "node_name": "974SJOLM8CIN_P5",
  "mqtt": {"address": "mqtt-mt.meteor-oi.re", "username": "…", "password": "…",
           "enabled": true, "proxy_to_client": true, "encryption": true, "json": true,
           "tls": false, "map_reporting": true,
           "map_report": {"publish_interval_secs": 3600, "position_precision": 15,
                          "should_report_location": true}},
  "position": {"broadcast_secs": 900, "fixed": true},
  "broker_matches": true, "creds_present": true, "needs_register": false, "applied": true }
```

- `broker_matches` : l'`address` du node correspond au broker cible demandé.
- `creds_present` : username **et** password non vides sur le node.
- `needs_register` : `!broker_matches || !creds_present` — l'appelant (installateur) décide
  alors de ne pas activer la passerelle et de renvoyer vers la page d'inscription.
- `applied` : `--apply` uniquement — la relecture post-reboot correspond à la cible.
- En cas d'échec (connexion BLE impossible, erreur inattendue) : `{"error": "…"}`.

**Exit code** : `0` si l'opération a pleinement réussi (`--inspect` lu, ou `--apply` vérifié) ;
`≠0` sinon (le JSON est émis dans tous les cas).

## Robustesse BLE (validée sur T114 réel)

- **Retry de connexion avec backoff** (4 tentatives, 3 s → 15 s) : le premier connect BLE
  échoue fréquemment (timeout), c'est normal.
- **Le node reboote après le commit** : le commit est lancé en *fire-and-forget* (thread
  daemon + join court — l'appel peut ne jamais rendre la main), puis l'outil attend le reboot
  et **rouvre une connexion fraîche pour relire et vérifier**. L'interface pré-reboot n'est
  jamais fermée (`close()` meshtastic gèle sur lien mort).
