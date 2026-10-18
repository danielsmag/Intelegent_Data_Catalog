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

# shellcheck disable=SC1091
source /opt/seed/spark-catalog-conf.sh

cat >/tmp/spark-query.properties <<EOF
spark.master=local[1]
spark.app.name=catalog-query
spark.driver.memory=1g
spark.driver.host=127.0.0.1
spark.driver.bindAddress=127.0.0.1
spark.ui.enabled=false
spark.sql.shuffle.partitions=1
spark.sql.ansi.enabled=true
EOF
write_spark_catalog_conf /tmp/spark-catalog.properties
cat /tmp/spark-catalog.properties >>/tmp/spark-query.properties

export PYSPARK_PYTHON=python3
export QUERY_PORT="${query_port}"
exec /opt/spark/bin/spark-submit \
  --packages "${ICEBERG_PACKAGES}" \
  --properties-file /tmp/spark-query.properties \
  --conf "spark.sql.catalog.${catalog_name}.credential=${POLARIS_CLIENT_ID}:${POLARIS_CLIENT_SECRET}" \
  /opt/seed/query_server.py
