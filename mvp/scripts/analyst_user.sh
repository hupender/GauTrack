#!/usr/bin/env bash
# Create (or reset the password of) the read-only database login used by
# analysis tools: Power BI Desktop, Excel, Metabase, psql.
#
# Why a separate login instead of reusing the application's:
#   - the app's login can INSERT and UPDATE; an analyst's must not.
#   - it is granted SELECT on operational tables only. It has NO access to
#     `users` (password hashes), `sessions` (live login tokens), `devices` or
#     `login_attempts`. See migration 0003_exports_and_analytics.py.
#   - it can be revoked on its own, without touching the running service.
#
# Usage:  make analyst-user
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[ -f "$HERE/.env" ] && set -a && . "$HERE/.env" && set +a

DB_NAME="${POSTGRES_DB:-gautrack}"
OWNER_USER="${POSTGRES_OWNER_USER:-gautrack_owner}"
HOST="${POSTGRES_HOST:-127.0.0.1}"
PORT="${POSTGRES_PORT:-55432}"
SOCKDIR="${DEV_PG_SOCKDIR:-/tmp/gautrack-pg}"
RO_USER="gautrack_ro"
# note: `tr </dev/urandom | head -c` raises SIGPIPE under `set -o pipefail`
RO_PW="$(python3 -c 'import secrets,string; print("".join(secrets.choice(string.ascii_letters+string.digits) for _ in range(24)))')"

find_pgbin() {
  command -v psql >/dev/null 2>&1 && { dirname "$(command -v psql)"; return; }
  for d in /opt/homebrew/opt/postgresql@16/bin /usr/local/opt/postgresql@16/bin; do
    [ -x "$d/psql" ] && { echo "$d"; return; }
  done
  echo "[analyst] psql not found. brew install postgresql@16" >&2; exit 1
}
PGBIN="$(find_pgbin)"

# Connect the same way scripts/dev_db.sh does: over the local unix socket as the
# superuser role, which the dev cluster trusts. On a real server this script is
# run by the DBA with their own credentials instead.
if [ -S "$SOCKDIR/.s.PGSQL.$PORT" ]; then
  psql_admin() { "$PGBIN/psql" -h "$SOCKDIR" -p "$PORT" -U postgres -v ON_ERROR_STOP=1 -X -q "$@"; }
elif command -v docker >/dev/null 2>&1 && ( cd "$HERE" && docker compose -f docker-compose.dev.yml ps db 2>/dev/null | grep -q Up ); then
  psql_admin() { ( cd "$HERE" && docker compose -f docker-compose.dev.yml exec -T db psql -U "$OWNER_USER" -v ON_ERROR_STOP=1 -X -q "$@" ); }
else
  echo "[analyst] the database does not appear to be running. Start it first:  make db-up" >&2
  exit 1
fi

echo "[analyst] creating/updating read-only login '$RO_USER' on $DB_NAME"
psql_admin -d postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname='$RO_USER'" | grep -q 1 \
  || psql_admin -d postgres -c "CREATE ROLE \"$RO_USER\" LOGIN PASSWORD '$RO_PW'"
psql_admin -d postgres -c "ALTER ROLE \"$RO_USER\" PASSWORD '$RO_PW'"

# Grants live in the migration so they stay in one place; re-apply them here so
# a login created after the migration ran still gets them.
psql_admin -d "$DB_NAME" -v ON_ERROR_STOP=1 <<SQL
GRANT CONNECT ON DATABASE "$DB_NAME" TO "$RO_USER";
GRANT USAGE ON SCHEMA public TO "$RO_USER";
GRANT SELECT ON ulbs, owners, animals, events, fines, shelters, fine_schedule TO "$RO_USER";
GRANT SELECT ON v_daily_counts TO "$RO_USER";
REVOKE ALL ON users, sessions, login_attempts, devices FROM "$RO_USER";
ALTER ROLE "$RO_USER" SET default_transaction_read_only = on;
SQL

cat <<EOF

  Read-only analytics login ready.

    Host      $HOST
    Port      $PORT
    Database  $DB_NAME
    Username  $RO_USER
    Password  $RO_PW

  Power BI Desktop:  Get Data > PostgreSQL database > Server "$HOST:$PORT", Database "$DB_NAME"
  Excel:             Data > Get Data > From Database > From PostgreSQL Database
  psql:              PGPASSWORD='$RO_PW' psql -h $HOST -p $PORT -U $RO_USER -d $DB_NAME

  This password is shown once and is not stored anywhere. Re-run 'make analyst-user'
  to issue a new one (which immediately invalidates the old).

  It can only read, and only these tables: ulbs, owners, animals, events, fines,
  shelters, fine_schedule, v_daily_counts. It cannot read users or sessions, and
  it cannot write anything.

EOF
