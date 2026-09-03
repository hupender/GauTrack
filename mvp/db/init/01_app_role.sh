#!/bin/sh
# Runs once, inside the postgres container, on first boot of an empty data dir.
#
# Creates the *low-privilege* role the API connects as.  The schema is owned by
# ${POSTGRES_USER} (the "owner" role) which is what Alembic uses.  Table-level
# grants for the app role are issued by the Alembic migration, so that new
# tables cannot accidentally be created without a deliberate grant.
set -eu

: "${APP_DB_USER:?APP_DB_USER not set}"
: "${APP_DB_PASSWORD:?APP_DB_PASSWORD not set}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE ROLE "${APP_DB_USER}" LOGIN PASSWORD '${APP_DB_PASSWORD}';
    -- app role may use the schema but may NOT create objects in it
    GRANT CONNECT ON DATABASE "${POSTGRES_DB}" TO "${APP_DB_USER}";
    GRANT USAGE ON SCHEMA public TO "${APP_DB_USER}";
    REVOKE CREATE ON SCHEMA public FROM PUBLIC;
    -- pg_trgm powers the owner near-duplicate check (name+village similarity)
    CREATE EXTENSION IF NOT EXISTS pg_trgm;
    -- so `make test` can create a throwaway database as the owner role
    ALTER ROLE "${POSTGRES_USER}" CREATEDB;
EOSQL

# every database created later (e.g. the test database) inherits pg_trgm
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname template1 \
     -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;"
