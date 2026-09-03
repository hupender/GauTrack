#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Development Postgres bootstrapper.
#
#   scripts/dev_db.sh start|stop|status|psql|reset
#
# Preference order:
#   1. `docker compose -f docker-compose.dev.yml`  (matches production exactly)
#   2. a private Postgres 16 cluster in mvp/.pgdata driven by pg_ctl
#      (used when Docker is not installed / not running on this machine)
#
# The pg_ctl path deliberately uses its OWN data directory inside the repo and a
# non-default port, so it never touches an existing Postgres on the machine and
# never registers a launchd/systemd service.
# ---------------------------------------------------------------------------
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PGDATA="$HERE/.pgdata"
# The socket dir must not contain spaces: pg_ctl -o splits its argument on
# whitespace, and the repo path may well contain a space.
SOCKDIR="${DEV_PG_SOCKDIR:-/tmp/gautrack-pg}"
PORT="${DEV_PG_PORT:-55432}"

DB_NAME="${POSTGRES_DB:-gautrack}"
OWNER_USER="${POSTGRES_OWNER_USER:-gautrack_owner}"
APP_USER="${POSTGRES_APP_USER:-gautrack_app}"
OWNER_PW="${POSTGRES_PASSWORD:-devowner}"
APP_PW="${APP_DB_PASSWORD:-devapp}"

# Load .env if present so passwords match what the API will use.
if [ -f "$HERE/.env" ]; then
  set -a; . "$HERE/.env"; set +a
  DB_NAME="${POSTGRES_DB:-$DB_NAME}"
  OWNER_USER="${POSTGRES_OWNER_USER:-$OWNER_USER}"
  APP_USER="${POSTGRES_APP_USER:-$APP_USER}"
  OWNER_PW="${POSTGRES_PASSWORD:-$OWNER_PW}"
  APP_PW="${APP_DB_PASSWORD:-$APP_PW}"
  PORT="${POSTGRES_PORT:-$PORT}"
fi

have_docker() { command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; }

find_pgbin() {
  for c in \
      "$(brew --prefix postgresql@16 2>/dev/null || true)/bin" \
      /opt/homebrew/opt/postgresql@16/bin \
      /usr/local/opt/postgresql@16/bin \
      /usr/lib/postgresql/16/bin ; do
    [ -x "$c/pg_ctl" ] && { echo "$c"; return 0; }
  done
  command -v pg_ctl >/dev/null 2>&1 && { dirname "$(command -v pg_ctl)"; return 0; }
  return 1
}

mode() { if have_docker; then echo docker; else echo pgctl; fi; }

# --------------------------------------------------------------------------- docker
docker_start() {
  ( cd "$HERE" && docker compose -f docker-compose.dev.yml up -d )
  echo "[dev_db] waiting for postgres..."
  for _ in $(seq 1 60); do
    if ( cd "$HERE" && docker compose -f docker-compose.dev.yml exec -T db pg_isready -q -U "$OWNER_USER" -d "$DB_NAME" ) 2>/dev/null; then
      echo "[dev_db] ready on 127.0.0.1:55432 (docker)"; return 0
    fi
    sleep 1
  done
  echo "[dev_db] timed out waiting for postgres" >&2; return 1
}
docker_stop() { ( cd "$HERE" && docker compose -f docker-compose.dev.yml down ); }
docker_reset() { ( cd "$HERE" && docker compose -f docker-compose.dev.yml down -v ); }

# --------------------------------------------------------------------------- pg_ctl
pgctl_start() {
  PGBIN="$(find_pgbin)" || { echo "[dev_db] no Postgres 16 found. brew install postgresql@16" >&2; exit 1; }
  export PATH="$PGBIN:$PATH"
  # macOS: without an explicit locale the postmaster "becomes multithreaded
  # during startup" and refuses to boot (Homebrew caveat for postgresql@16).
  export LC_ALL="${LC_ALL:-en_US.UTF-8}" LANG="${LANG:-en_US.UTF-8}"

  if [ ! -s "$PGDATA/PG_VERSION" ]; then
    echo "[dev_db] initdb -> $PGDATA"
    mkdir -p "$PGDATA"
    # `trust` on the unix socket only; the cluster listens on 127.0.0.1 with
    # scram-sha-256, and lives in a repo-local directory.
    "$PGBIN/initdb" -D "$PGDATA" -U postgres \
        --auth-local=trust --auth-host=scram-sha-256 -E UTF8 >/dev/null
    mkdir -p "$SOCKDIR"
  fi
  mkdir -p "$SOCKDIR"

  if "$PGBIN/pg_ctl" -D "$PGDATA" status >/dev/null 2>&1; then
    echo "[dev_db] already running"
  else
    "$PGBIN/pg_ctl" -D "$PGDATA" -l "$PGDATA/server.log" -w \
      -o "-p $PORT -k $SOCKDIR -c listen_addresses=127.0.0.1" start
  fi

  local psql="$PGBIN/psql -h $SOCKDIR -p $PORT -U postgres -v ON_ERROR_STOP=1 -X -q"
  # roles + database + extension, idempotent
  $psql -d postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname='$OWNER_USER'" | grep -q 1 || \
    $psql -d postgres -c "CREATE ROLE \"$OWNER_USER\" LOGIN PASSWORD '$OWNER_PW'"
  $psql -d postgres -c "ALTER ROLE \"$OWNER_USER\" PASSWORD '$OWNER_PW'"
  $psql -d postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname='$APP_USER'" | grep -q 1 || \
    $psql -d postgres -c "CREATE ROLE \"$APP_USER\" LOGIN PASSWORD '$APP_PW'"
  $psql -d postgres -c "ALTER ROLE \"$APP_USER\" PASSWORD '$APP_PW'"
  # the owner role needs CREATEDB so `make test` can spin up a throwaway database
  $psql -d postgres -c "ALTER ROLE \"$OWNER_USER\" CREATEDB"
  # put pg_trgm in template1 so every database created later inherits it
  # (CREATE EXTENSION needs superuser, which the owner role deliberately is not)
  $psql -d template1 -c "CREATE EXTENSION IF NOT EXISTS pg_trgm"
  $psql -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" | grep -q 1 || \
    $psql -d postgres -c "CREATE DATABASE \"$DB_NAME\" OWNER \"$OWNER_USER\""
  $psql -d "$DB_NAME" -c "GRANT CONNECT ON DATABASE \"$DB_NAME\" TO \"$APP_USER\";
                          GRANT USAGE ON SCHEMA public TO \"$APP_USER\";
                          REVOKE CREATE ON SCHEMA public FROM PUBLIC;
                          ALTER SCHEMA public OWNER TO \"$OWNER_USER\";
                          CREATE EXTENSION IF NOT EXISTS pg_trgm;"
  echo "[dev_db] ready on 127.0.0.1:$PORT (pg_ctl, data dir $PGDATA)"
}
pgctl_stop() {
  PGBIN="$(find_pgbin)" || exit 0
  "$PGBIN/pg_ctl" -D "$PGDATA" -m fast stop 2>/dev/null || echo "[dev_db] not running"
}
pgctl_reset() { pgctl_stop || true; rm -rf "$PGDATA"; echo "[dev_db] wiped $PGDATA"; }
pgctl_psql() {
  PGBIN="$(find_pgbin)"; exec "$PGBIN/psql" -h "$SOCKDIR" -p "$PORT" -U postgres -d "$DB_NAME" "$@"
}

MODE="$(mode)"
CMD="${1:-start}"; shift || true

if [ "$MODE" = docker ]; then
  case "$CMD" in
    start)  docker_start ;;
    stop)   docker_stop ;;
    reset)  docker_reset ;;
    status) ( cd "$HERE" && docker compose -f docker-compose.dev.yml ps ) ;;
    psql)   ( cd "$HERE" && docker compose -f docker-compose.dev.yml exec db psql -U "$OWNER_USER" -d "$DB_NAME" "$@" ) ;;
    *) echo "usage: $0 {start|stop|reset|status|psql}" >&2; exit 2 ;;
  esac
else
  case "$CMD" in
    start)  pgctl_start ;;
    stop)   pgctl_stop ;;
    reset)  pgctl_reset ;;
    status) PGBIN="$(find_pgbin)"; "$PGBIN/pg_ctl" -D "$PGDATA" status ;;
    psql)   pgctl_psql "$@" ;;
    *) echo "usage: $0 {start|stop|reset|status|psql}" >&2; exit 2 ;;
  esac
fi
