#!/usr/bin/env bash
# Keep one Spark session up and serve read-only SQL over HTTP.

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
: "${ICEBERG_PACKAGES:?}"

catalog_name="${POLARIS_CATALOG_NAME}"
query_port="${QUERY_PORT:-8091}"

cat >/tmp/spark-query.properties <<EOF
spark.master=local[1]
spark.app.name=catalog-query
spark.driver.memory=1g
spark.driver.host=127.0.0.1
spark.driver.bindAddress=127.0.0.1
spark.ui.enabled=false
spark.sql.shuffle.partitions=1
spark.sql.ansi.enabled=true
spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions
spark.sql.defaultCatalog=${catalog_name}
spark.sql.catalog.${catalog_name}=org.apache.iceberg.spark.SparkCatalog
spark.sql.catalog.${catalog_name}.type=rest
spark.sql.catalog.${catalog_name}.uri=${POLARIS_URI}
spark.sql.catalog.${catalog_name}.warehouse=${POLARIS_CATALOG_NAME}
spark.sql.catalog.${catalog_name}.scope=PRINCIPAL_ROLE:ALL
spark.sql.catalog.${catalog_name}.oauth2-server-uri=${POLARIS_TOKEN_URI}
spark.sql.catalog.${catalog_name}.header.X-Iceberg-Access-Delegation=vended-credentials
spark.sql.catalog.${catalog_name}.header.Polaris-Realm=${POLARIS_REALM}
spark.jars.ivy=/tmp/ivy
spark.redaction.regex=(?i)secret|password|token|credential
EOF

export PYSPARK_PYTHON=python3
export QUERY_PORT="${query_port}"
exec /opt/spark/bin/spark-submit \
  --packages "${ICEBERG_PACKAGES}" \
  --properties-file /tmp/spark-query.properties \
  --conf "spark.sql.catalog.${catalog_name}.credential=${POLARIS_CLIENT_ID}:${POLARIS_CLIENT_SECRET}" \
  /opt/seed/query_server.py
