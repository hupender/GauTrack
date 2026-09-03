#!/usr/bin/env bash
# Run Alembic migrations + demo seed against a remote Postgres (e.g. Supabase).
# Usage:
#   1. Fill in POSTGRES_PASSWORD in mvp/.env.supabase (Supabase project DB password).
#   2. bash scripts/seed_supabase.sh
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENVF="$HERE/.env.supabase"

if [ ! -f "$ENVF" ]; then
  echo "missing $ENVF — copy from .env.supabase.example and set POSTGRES_PASSWORD" >&2
  exit 2
fi

set -a
# shellcheck disable=SC1090
. "$ENVF"
set +a

if [ -z "${POSTGRES_PASSWORD:-}" ] || [ "$POSTGRES_PASSWORD" = "REPLACE_WITH_SUPABASE_DB_PASSWORD" ]; then
  echo "Set POSTGRES_PASSWORD in $ENVF to your Supabase database password, then run again." >&2
  exit 2
fi

if [ ! -x "$HERE/.venv/bin/python" ]; then
  echo "Run 'make venv' in mvp/ first." >&2
  exit 2
fi

PY="$HERE/.venv/bin/python"
ALEMBIC="$HERE/.venv/bin/alembic"

export SECRET_KEY="${SECRET_KEY:-seed-remote-run-only}"
export SEED_DEMO="${SEED_DEMO:-1}"

cd "$HERE/api"
echo "[seed_supabase] migrating on ${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB} ..."
"$ALEMBIC" upgrade head
echo "[seed_supabase] seeding demo data (SEED_PASSWORD=${SEED_PASSWORD:-<random>}) ..."
"$PY" -m seed
echo "[seed_supabase] done."
