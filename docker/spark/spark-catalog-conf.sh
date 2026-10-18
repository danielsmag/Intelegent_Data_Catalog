#!/usr/bin/env bash
# Iceberg REST settings shared by the SQL server and notebook kernels.
# Caller exports POLARIS_CATALOG_NAME, POLARIS_URI, POLARIS_TOKEN_URI, POLARIS_REALM.

write_spark_catalog_conf() {
  local dest="$1"
  : "${POLARIS_CATALOG_NAME:?}"
  : "${POLARIS_URI:?}"
  : "${POLARIS_TOKEN_URI:?}"
  : "${POLARIS_REALM:?}"
  local catalog_name="${POLARIS_CATALOG_NAME}"
  cat >"$dest" <<EOF
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
}
