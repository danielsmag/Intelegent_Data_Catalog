#!/usr/bin/env bash
# Jupyter Kernel Gateway. One PySpark kernel per notebook, culled when idle.

set -euo pipefail

if [[ ! -f /bootstrap/notebook.env ]]; then
  echo "missing /bootstrap/notebook.env; polaris-setup did not finish" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1091
source /bootstrap/notebook.env
set +a

: "${POLARIS_CLIENT_ID:?}"
: "${POLARIS_CLIENT_SECRET:?}"
: "${POLARIS_CATALOG_NAME:?}"
: "${POLARIS_REALM:?}"
: "${POLARIS_URI:?}"
: "${POLARIS_TOKEN_URI:?}"
: "${NOTEBOOK_GATEWAY_TOKEN:?}"

# shellcheck disable=SC1091
source /opt/spark/spark-catalog-conf.sh
write_spark_catalog_conf /tmp/spark-catalog.properties
chmod 644 /tmp/spark-catalog.properties

export KG_ENV_WHITELIST="CATALOG_USER,CATALOG_ENGINES_JSON,CATALOG_SPARK_CATALOGS_JSON"
export KG_ENV_PROCESS_WHITELIST="SPARK_HOME,JAVA_HOME,PYSPARK_PYTHON,PYSPARK_DRIVER_PYTHON"
export KG_AUTH_TOKEN="${NOTEBOOK_GATEWAY_TOKEN}"

exec jupyter kernelgateway \
  --KernelGatewayApp.ip=0.0.0.0 \
  --KernelGatewayApp.port=8888 \
  --KernelGatewayApp.auth_token="${NOTEBOOK_GATEWAY_TOKEN}" \
  --KernelGatewayApp.default_kernel_name=pyspark \
  --KernelGatewayApp.allow_origin="" \
  --KernelGatewayApp.max_kernels=8 \
  --MappingKernelManager.cull_idle_timeout=1800 \
  --MappingKernelManager.cull_interval=60 \
  --MappingKernelManager.cull_connected=False
