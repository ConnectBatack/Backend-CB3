#!/usr/bin/env bash
set -euo pipefail

echo "[batack-api] Application des migrations Alembic..."
alembic upgrade head

if [ "${SEED_ON_STARTUP:-false}" = "true" ]; then
  echo "[batack-api] Exécution du seed initial (idempotent)..."
  python -m scripts.seed_db
fi

echo "[batack-api] Démarrage du serveur..."
exec "$@"

