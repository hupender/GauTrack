#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Nightly backup: a full database dump plus the photo directory.
#
#   scripts/backup.sh                    -> ./backups/gautrack-<stamp>.sql.gz
#   BACKUP_DIR=/backups scripts/backup.sh
#
# If `age` (https://age-encryption.org) is installed AND $BACKUP_AGE_RECIPIENT is
# set, the dump is encrypted at rest. Do that in production: a plain pg_dump of
# this database contains every cattle keeper's phone number.
# ---------------------------------------------------------------------------
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[ -f "$HERE/.env" ] && { set -a; . "$HERE/.env"; set +a; }

# Find the Postgres client tools even when they are not on PATH (Homebrew keeps
# postgresql@16 "keg-only", i.e. deliberately off PATH).
for c in "$(brew --prefix postgresql@16 2>/dev/null || true)/bin" \
         /opt/homebrew/opt/postgresql@16/bin /usr/local/opt/postgresql@16/bin \
         /usr/lib/postgresql/16/bin ; do
  [ -x "$c/pg_dump" ] && { PATH="$c:$PATH"; break; }
done
export PATH

BACKUP_DIR="${BACKUP_DIR:-$HERE/backups}"
STAMP="$(date -u +%Y%m%d-%H%M)"
mkdir -p "$BACKUP_DIR"

DB="${POSTGRES_DB:-gautrack}"
OWNER="${POSTGRES_OWNER_USER:-gautrack_owner}"
HOSTP="${POSTGRES_HOST:-127.0.0.1}"
PORT="${POSTGRES_PORT:-55432}"
PHOTOS="${PHOTO_DIR:-$HERE/data/photos}"

DUMP="$BACKUP_DIR/gautrack-$STAMP.sql.gz"

pg_dump_cmd() {
  if command -v docker >/dev/null 2>&1 && docker compose -f "$HERE/docker-compose.yml" ps db >/dev/null 2>&1; then
    docker compose -f "$HERE/docker-compose.yml" exec -T db pg_dump -U "$OWNER" -d "$DB" --clean --if-exists
  else
    PGPASSWORD="${POSTGRES_PASSWORD:-}" pg_dump -h "$HOSTP" -p "$PORT" -U "$OWNER" -d "$DB" --clean --if-exists
  fi
}

echo "[backup] dumping $DB -> $DUMP"
if command -v age >/dev/null 2>&1 && [ -n "${BACKUP_AGE_RECIPIENT:-}" ]; then
  pg_dump_cmd | gzip -9 | age -r "$BACKUP_AGE_RECIPIENT" > "$DUMP.age"
  DUMP="$DUMP.age"
  echo "[backup] encrypted for $BACKUP_AGE_RECIPIENT"
else
  pg_dump_cmd | gzip -9 > "$DUMP"
  echo "[backup] WARNING: not encrypted. Install age and set BACKUP_AGE_RECIPIENT before production."
fi

if [ -d "$PHOTOS" ]; then
  PHOTO_TAR="$BACKUP_DIR/photos-$STAMP.tar.gz"
  tar -czf "$PHOTO_TAR" -C "$(dirname "$PHOTOS")" "$(basename "$PHOTOS")"
  echo "[backup] photos -> $PHOTO_TAR"
fi

# a backup you cannot prove is intact is not a backup
shasum -a 256 "$DUMP" >> "$BACKUP_DIR/SHA256SUMS.txt" 2>/dev/null || \
  sha256sum "$DUMP" >> "$BACKUP_DIR/SHA256SUMS.txt"

# retention: keep 14 daily copies
find "$BACKUP_DIR" -name 'gautrack-*.sql.gz*' -mtime +14 -delete 2>/dev/null || true
find "$BACKUP_DIR" -name 'photos-*.tar.gz' -mtime +14 -delete 2>/dev/null || true

echo "[backup] done. Copy $BACKUP_DIR to a second machine:"
echo "         rsync -az --delete $BACKUP_DIR/ backup@second-host:/srv/gautrack-backups/"
