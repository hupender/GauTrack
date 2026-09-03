#!/bin/sh
set -eu
cd /app

echo "[entrypoint] running migrations..."
alembic upgrade head

if [ "${SEED_DEMO:-0}" = "1" ]; then
  echo "[entrypoint] SEED_DEMO=1 -> seeding demo data"
  python -m seed
fi

echo "[entrypoint] starting uvicorn"
exec uvicorn main:app \
  --host 0.0.0.0 --port 8000 \
  --proxy-headers --forwarded-allow-ips='*' \
  --log-level "${LOG_LEVEL:-info}"
