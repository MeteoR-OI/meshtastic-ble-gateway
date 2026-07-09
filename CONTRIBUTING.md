# Contribuer

Merci de contribuer à `meshtastic-ble-gateway`. Ce guide couvre l'essentiel ; le **contexte
technique interne** (invariants, décisions verrouillées, architecture détaillée) est dans
[`CLAUDE.md`](CLAUDE.md).

## Mise en place

```bash
python -m venv .venv
./.venv/bin/pip install -e ".[dev]"
```

## Tests & qualité (obligatoire)

- **100 % branch coverage** — c'est un standard du repo, pas une cible souple :
  ```bash
  pytest                       # unitaires + couverture (config dans pyproject.toml)
  ```
  Les I/O externes (BLE, MQTT, fork, sockets, subprocess) sont **injectés derrière des
  fabriques/seams** → testables sans matériel. Les frontières OS sont marquées `# pragma: no cover`.
- **Vérifier aussi en Python 3.9** (plancher, cible Raspberry Pi OS Bullseye) :
  ```bash
  docker run --rm -v "$PWD":/app -w /app python:3.9 bash -c "pip install -q -e '.[dev]' && pytest -q"
  ```
- **Intégration** (broker/fork/HTTP réels) :
  ```bash
  docker compose -f poc/docker-compose.yml up -d && pytest tests/integration --no-cov
  ```
- **La couverture ne prouve pas la correction** : tester le **chemin de déploiement réel** (env /
  systemd), pas seulement un chemin qui touche les lignes.

## Style & conventions

- En-tête `SPDX-License-Identifier: AGPL-3.0-or-later` sur chaque fichier source.
- Écrire du code qui **ressemble au code environnant** (nommage, densité de commentaires, idiomes).
- **Documentation publique agnostique** : pas de noms d'hôtes/nodes/MAC/broker réels — utiliser des
  placeholders (`<MAC-du-node>`, `<broker-host>`, `!a1b2c3d4`, canal `MonCanal`).
- **Secrets** (creds MQTT, token API) : uniquement dans le fichier systemd sur l'hôte, jamais dans
  le repo.
- Ne pas modifier la config d'un node déjà en place — la **vérifier** en read-only.

## Commits & releases

- Message de commit clair (`type(scope): résumé`), en français.
- Auteur = mainteneur (email GitHub-vérifié). **Pas de trailer `Co-Authored-By`** ; citer
  l'assistance en prose si pertinent (`Assisté par Claude Code (Anthropic).`).
- Chaque version : bump `pyproject.toml` + `src/mbg/__init__.py`, tag `vX.Y.Z`, entrée dans
  [`CHANGELOG.md`](CHANGELOG.md), et [release GitHub](https://github.com/MeteoR-OI/meshtastic-ble-gateway/releases).
- **On ne merge que ce qui a été validé** (tests verts **et**, pour les changements BLE/déploiement,
  vérifié sur du matériel réel).
