#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Restore from a backup produced by scripts/backup.sh.
#
#   scripts/restore.sh backups/gautrack-20260817-2100.sql.gz
#   scripts/restore.sh backups/gautrack-20260817-2100.sql.gz.age   (needs age + key)
#
# PRACTISE THIS BEFORE YOU NEED IT. A restore you have never rehearsed is a
# guess. After restoring, ALWAYS run scripts/verify_chain.py: the hash chain
# tells you whether what came back is exactly what went in.
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

FILE="${1:-}"
[ -n "$FILE" ] || { echo "usage: $0 <backup file>" >&2; exit 2; }
[ -f "$FILE" ] || { echo "no such file: $FILE" >&2; exit 2; }

DB="${POSTGRES_DB:-gautrack}"
OWNER="${POSTGRES_OWNER_USER:-gautrack_owner}"
HOSTP="${POSTGRES_HOST:-127.0.0.1}"
PORT="${POSTGRES_PORT:-55432}"

echo "This OVERWRITES the '$DB' database on $HOSTP:$PORT."
printf "Type the database name to confirm: "
read -r confirm
[ "$confirm" = "$DB" ] || { echo "aborted"; exit 1; }

stream() {
  case "$FILE" in
    *.age) age -d -i "${BACKUP_AGE_IDENTITY:-$HOME/.age/gautrack.key}" "$FILE" | gunzip ;;
    *.gz)  gunzip -c "$FILE" ;;
    *)     cat "$FILE" ;;
  esac
}

if command -v docker >/dev/null 2>&1 && docker compose -f "$HERE/docker-compose.yml" ps db >/dev/null 2>&1; then
  stream | docker compose -f "$HERE/docker-compose.yml" exec -T db psql -v ON_ERROR_STOP=1 -U "$OWNER" -d "$DB"
else
  stream | PGPASSWORD="${POSTGRES_PASSWORD:-}" psql -v ON_ERROR_STOP=1 -h "$HOSTP" -p "$PORT" -U "$OWNER" -d "$DB"
fi

echo "[restore] done. Now prove it is intact:"
echo "          python3 $HERE/scripts/verify_chain.py"
