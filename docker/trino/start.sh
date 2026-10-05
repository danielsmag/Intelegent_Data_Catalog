#!/bin/bash
# Write Trino catalogs from the Polaris bootstrap, then start the coordinator.

set -euo pipefail

if [[ ! -f /bootstrap/client.env ]]; then
  echo "missing /bootstrap/client.env; polaris-setup did not finish" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1091
source /bootstrap/client.env
set +a

: "${POLARIS_CLIENT_ID:?}"
: "${POLARIS_CLIENT_SECRET:?}"
: "${POLARIS_CATALOG_NAME:?}"
: "${POLARIS_REALM:?}"
: "${POLARIS_URI:?}"
: "${POLARIS_TOKEN_URI:?}"

mkdir -p /etc/trino/catalog

cat >/etc/trino/catalog/polaris.properties <<EOF
connector.name=iceberg
iceberg.catalog.type=rest
iceberg.rest-catalog.uri=${POLARIS_URI}
iceberg.rest-catalog.warehouse=${POLARIS_CATALOG_NAME}
iceberg.rest-catalog.security=OAUTH2
iceberg.rest-catalog.oauth2.credential=${POLARIS_CLIENT_ID}:${POLARIS_CLIENT_SECRET}
iceberg.rest-catalog.oauth2.scope=PRINCIPAL_ROLE:ALL
iceberg.rest-catalog.oauth2.server-uri=${POLARIS_TOKEN_URI}
iceberg.rest-catalog.vended-credentials-enabled=true
fs.native-s3.enabled=true
s3.endpoint=http://minio:9000
s3.path-style-access=true
s3.region=${AWS_REGION:-us-west-2}
s3.aws-access-key=${MINIO_ROOT_USER:-polaris_root}
s3.aws-secret-key=${MINIO_ROOT_PASSWORD:-polaris_pass}
EOF

cat >/etc/trino/catalog/appdb.properties <<EOF
connector.name=postgresql
connection-url=jdbc:postgresql://postgres:5432/${SOURCE_DB_NAME:-sales}
connection-user=${SOURCE_DB_USER:-sales}
connection-password=${SOURCE_DB_PASSWORD:-sales}
EOF

exec /usr/lib/trino/bin/run-trino
