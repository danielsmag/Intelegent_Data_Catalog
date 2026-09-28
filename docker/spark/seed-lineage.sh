#!/usr/bin/env bash
# Wait for Marquez, load the sales sample into Polaris, then require lineage.

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
: "${OPENLINEAGE_NAMESPACE:?}"
: "${POLARIS_URI:?}"
: "${POLARIS_TOKEN_URI:?}"
: "${MARQUEZ_URL:?}"
: "${ICEBERG_PACKAGES:?}"

catalog_name="${POLARIS_CATALOG_NAME}"

python3 - <<'PY'
import os
import time
import urllib.error
import urllib.request

url = os.environ["MARQUEZ_URL"].rstrip("/") + "/api/v1/namespaces"
for attempt in range(1, 61):
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            if response.status == 200:
                print("marquez ready")
                raise SystemExit(0)
    except (urllib.error.URLError, TimeoutError):
        print(f"waiting for marquez attempt={attempt}")
        time.sleep(2)
raise SystemExit("marquez api did not become ready")
PY

if [[ "${SEED_FORCE:-}" != "true" ]]; then
  if python3 /opt/seed/check_lineage.py --if-present; then
    echo "lineage already present; skipping seed"
    exit 0
  fi
fi

cat >/tmp/spark-lineage.properties <<EOF
spark.master=local[1]
spark.app.name=customer_spend
spark.driver.memory=1g
spark.driver.host=localhost
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
spark.extraListeners=io.openlineage.spark.agent.OpenLineageSparkListener
spark.openlineage.transport.type=http
spark.openlineage.transport.url=${MARQUEZ_URL}
spark.openlineage.namespace=${OPENLINEAGE_NAMESPACE}
spark.jars.ivy=/tmp/ivy
spark.redaction.regex=(?i)secret|password|token|credential
EOF

export PYSPARK_PYTHON=python3
/opt/spark/bin/spark-submit \
  --packages "${ICEBERG_PACKAGES}" \
  --properties-file /tmp/spark-lineage.properties \
  --conf "spark.sql.catalog.${catalog_name}.credential=${POLARIS_CLIENT_ID}:${POLARIS_CLIENT_SECRET}" \
  /opt/seed/seed_lineage.py

python3 /opt/seed/check_lineage.py
