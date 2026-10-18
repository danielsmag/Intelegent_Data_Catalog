#!/bin/bash
# Create the application databases on the shared Postgres server.
# Runs once, on first data-directory initialization.

set -euo pipefail

: "${POLARIS_DB_NAME:=POLARIS}"
: "${CATALOG_DB_USER:=catalog}"
: "${CATALOG_DB_PASSWORD:=catalog}"
: "${CATALOG_DB_NAME:=catalog}"
: "${SOURCE_DB_USER:=sales}"
: "${SOURCE_DB_PASSWORD:=sales}"
: "${SOURCE_DB_NAME:=sales}"
: "${MARQUEZ_DB_USER:=marquez}"
: "${MARQUEZ_DB_PASSWORD:=marquez}"
: "${MARQUEZ_DB_NAME:=marquez}"

psql_super() {
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" "$@"
}

create_database() {
  local owner="$1"
  local database="$2"
  psql_super --dbname postgres \
    --set=owner="$owner" \
    --set=database="$database" <<'SQL'
SELECT format('CREATE DATABASE %I OWNER %I', :'database', :'owner')
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = :'database')
\gexec
SQL
  psql_super --dbname "$database" --set=owner="$owner" <<'SQL'
ALTER SCHEMA public OWNER TO :"owner";
SQL
}

create_login_and_database() {
  local role="$1"
  local password="$2"
  local database="$3"
  psql_super --dbname postgres \
    --set=role="$role" \
    --set=password="$password" <<'SQL'
SELECT format('CREATE ROLE %I WITH LOGIN PASSWORD %L', :'role', :'password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'role')
\gexec
SQL
  create_database "$role" "$database"
}

if [ "$POLARIS_DB_NAME" != "postgres" ]; then
  create_database "$POSTGRES_USER" "$POLARIS_DB_NAME"
fi

create_login_and_database "$CATALOG_DB_USER" "$CATALOG_DB_PASSWORD" "$CATALOG_DB_NAME"
create_login_and_database "$SOURCE_DB_USER" "$SOURCE_DB_PASSWORD" "$SOURCE_DB_NAME"
create_login_and_database "$MARQUEZ_DB_USER" "$MARQUEZ_DB_PASSWORD" "$MARQUEZ_DB_NAME"

reader_password="${SOURCE_DB_READER_PASSWORD:-sales_reader}"
psql_super --dbname postgres \
  --set=role=sales_reader \
  --set=password="$reader_password" <<'SQL'
SELECT format('CREATE ROLE %I WITH LOGIN PASSWORD %L', :'role', :'password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'role')
\gexec
SQL
psql_super --dbname postgres \
  --set=role=sales_reader \
  --set=database="$SOURCE_DB_NAME" <<'SQL'
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', :'database', :'role')
\gexec
SQL

psql -v ON_ERROR_STOP=1 \
  --username "$SOURCE_DB_USER" \
  --dbname "$SOURCE_DB_NAME" \
  --file /opt/source-init/init.sql
