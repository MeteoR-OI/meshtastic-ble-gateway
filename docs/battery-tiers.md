# Paliers batterie + duty-cycle

**Opt-in** (`MBG_BATTERY_TIERS=true`, **nécessite le [monitoring](monitoring.md)** comme source de
batterie ; sinon un WARNING est loggé et l'option est ignorée). Le superviseur lit la dernière
batterie du node et **adapte le comportement** — plus la batterie baisse, moins on sollicite le
lien, pour préserver **la batterie du node** (un lien BLE permanent empêche son light-sleep).

## Paliers

| Batterie | Cadence de relevé | Lien BLE / proxy |
|---|---|---|
| ≥ 75 % | 15 min | connecté, proxy live |
| ≥ 50 % | 30 min | connecté, proxy live |
| ≥ 25 % | 60 min | connecté, proxy live |
| < 25 % | 1 relevé / fenêtre | **duty-cycle** : ON `MBG_DUTY_ON` (5 min) / OFF `MBG_DUTY_OFF` (30 min) |

> ⚠️ Quand les paliers sont actifs, **`MBG_MONITOR_INTERVAL` est ignoré** : la cadence de relevé
> suit le palier ci-dessus. Activer les paliers **ralentit donc le monitoring** (15 min au mieux)
> par rapport au défaut de 300 s — c'est volontaire (économie d'énergie). Les intervalles sont des
> constantes dans [`src/mbg/tiers.py`](../src/mbg/tiers.py).

## Comportement

- **Duty-cycle (< 25 %)** : le lien est **volontairement coupé** pendant le OFF (le node peut
  dormir) → **uplinks perdus** sur cette fenêtre (trous de flux **assumés**). Le OFF est une
  attente **watchdog-friendly** (il dépasse `WatchdogSec`, donc le superviseur continue de nourrir
  systemd — pas de faux redémarrage). Pendant le OFF, `GET /metrics` reste servi mais l'API de
  contrôle renvoie `503` (aucun worker).
- **Hystérésis** (`MBG_TIER_HYSTERESIS`, 3 % par défaut) : on descend d'un palier au seuil **nominal**
  mais on ne remonte qu'**après seuil + hystérésis** → pas de flapping (surtout autour du seuil
  critique 25 %).
- **Télémétrie au changement de mode** : à chaque transition de palier, la session suivante force un
  `sendTelemetry` (broadcast) → la batterie fraîche est **diffusée sur le mesh / MeshForge**,
  annonçant le changement. Chaque transition est loguée (`palier batterie → <PALIER> (batterie=n%)`).

## Contraintes

- Garder `MBG_MAX_RECONNECT_DELAY` < `WatchdogSec` (défaut 30 < 120).
- Le duty-cycle < 25 % s'appuie sur l'isolation de process (le superviseur pilote le cycle de vie
  du worker) — voir [resilience.md](resilience.md).

Variables : [configuration.md](configuration.md#paliers-batterie--duty-cycle-opt-in).
