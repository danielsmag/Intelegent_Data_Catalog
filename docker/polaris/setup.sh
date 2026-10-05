#!/bin/sh
# Create the Polaris catalog, a Spark principal, and write client credentials.

set -eu

apk add --no-cache jq postgresql-client

require_env() {
  name=$1
  eval "value=\${$name:-}"
  if [ -z "$value" ]; then
    echo "missing environment variable ${name}" >&2
    exit 1
  fi
}

require_env POLARIS_REALM
require_env POLARIS_ROOT_CLIENT_ID
require_env POLARIS_ROOT_CLIENT_SECRET
require_env POLARIS_CATALOG_NAME
require_env WAREHOUSE_BUCKET
require_env S3_ENDPOINT
require_env S3_ENDPOINT_INTERNAL
require_env AWS_REGION
require_env OPENLINEAGE_NAMESPACE
require_env POLARIS_DB_USER
require_env POLARIS_DB_PASSWORD
require_env POLARIS_DB_NAME

BASE_URL="http://polaris:8181"
PRINCIPAL_NAME="spark_lineage"
PRINCIPAL_ROLE="spark_lineage_role"
CATALOG_ROLE="spark_catalog_role"
BODY_FILE="/tmp/polaris-body.json"

echo "requesting root token for realm ${POLARIS_REALM}"
token_json=$(curl --fail-with-body --silent --show-error \
  -X POST "${BASE_URL}/api/catalog/v1/oauth/tokens" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "grant_type=client_credentials" \
  --data-urlencode "client_id=${POLARIS_ROOT_CLIENT_ID}" \
  --data-urlencode "client_secret=${POLARIS_ROOT_CLIENT_SECRET}" \
  --data-urlencode "scope=PRINCIPAL_ROLE:ALL")
TOKEN=$(printf '%s' "$token_json" | jq -er '.access_token')

call() {
  method=$1
  path=$2
  body=${3-}
  if [ -n "$body" ]; then
    curl --silent --show-error --output "$BODY_FILE" --write-out "%{http_code}" \
      -X "$method" "${BASE_URL}${path}" \
      -H "Authorization: Bearer ${TOKEN}" \
      -H "Polaris-Realm: ${POLARIS_REALM}" \
      -H "Accept: application/json" \
      -H "Content-Type: application/json" \
      -d "$body"
  else
    curl --silent --show-error --output "$BODY_FILE" --write-out "%{http_code}" \
      -X "$method" "${BASE_URL}${path}" \
      -H "Authorization: Bearer ${TOKEN}" \
      -H "Polaris-Realm: ${POLARIS_REALM}" \
      -H "Accept: application/json"
  fi
}

require_status() {
  code=$1
  shift
  for ok in "$@"; do
    if [ "$code" = "$ok" ]; then
      return 0
    fi
  done
  echo "unexpected HTTP ${code} for Polaris request" >&2
  cat "$BODY_FILE" >&2 || true
  exit 1
}

catalog_path=$(printf '%s' "$POLARIS_CATALOG_NAME" | jq -sRr @uri)
location="s3://${WAREHOUSE_BUCKET}"

storage_config=$(jq -n \
  --arg location "$location" \
  --arg endpoint "$S3_ENDPOINT" \
  --arg endpoint_internal "$S3_ENDPOINT_INTERNAL" \
  --arg region "$AWS_REGION" \
  '{
    storageType: "S3",
    allowedLocations: [$location],
    endpoint: $endpoint,
    endpointInternal: $endpoint_internal,
    pathStyleAccess: true,
    region: $region
  }')

echo "ensuring catalog ${POLARIS_CATALOG_NAME}"
code=$(call GET "/api/management/v1/catalogs/${catalog_path}")
if [ "$code" = "404" ]; then
  payload=$(jq -n \
    --arg name "$POLARIS_CATALOG_NAME" \
    --arg location "$location" \
    --argjson storage "$storage_config" \
    '{
      catalog: {
        name: $name,
        type: "INTERNAL",
        readOnly: false,
        properties: {"default-base-location": $location},
        storageConfigInfo: $storage
      }
    }')
  code=$(call POST "/api/management/v1/catalogs" "$payload")
  require_status "$code" 200 201
  echo "catalog created"
elif [ "$code" = "200" ]; then
  entity_version=$(jq -er '.entityVersion' "$BODY_FILE")
  payload=$(jq -n \
    --argjson version "$entity_version" \
    --argjson storage "$storage_config" \
    '{
      currentEntityVersion: $version,
      storageConfigInfo: $storage
    }')
  code=$(call PUT "/api/management/v1/catalogs/${catalog_path}" "$payload")
  require_status "$code" 200
  echo "catalog storage updated"
else
  require_status "$code" 200
fi

drop_broken_seed_table() {
  table_name=$1
  code=$(call GET "/api/catalog/v1/${catalog_path}/namespaces/sales/tables/${table_name}")
  if [ "$code" = "404" ]; then
    echo "seed table sales.${table_name} absent"
    return 0
  fi
  if [ "$code" = "200" ]; then
    echo "seed table sales.${table_name} reachable"
    return 0
  fi
  echo "seed table sales.${table_name} missing warehouse files; dropping catalog entity"
  PGPASSWORD="${POLARIS_DB_PASSWORD}" psql \
    --host postgres \
    --username "${POLARIS_DB_USER}" \
    --dbname "${POLARIS_DB_NAME}" \
    --set ON_ERROR_STOP=1 \
    --command "DELETE FROM polaris_schema.entities AS table_entity
USING polaris_schema.entities AS namespace_entity
WHERE namespace_entity.realm_id = table_entity.realm_id
  AND namespace_entity.type_code = 6
  AND namespace_entity.name = 'sales'
  AND table_entity.parent_id = namespace_entity.id
  AND table_entity.type_code = 7
  AND table_entity.name = '${table_name}';"
}

echo "checking seed tables against warehouse"
drop_broken_seed_table customers
drop_broken_seed_table orders
drop_broken_seed_table customer_spend

read_env_value() {
  key=$1
  file=$2
  line=$(grep "^${key}=" "$file" | head -n 1 || true)
  printf '%s' "${line#"${key}="}"
}

echo "ensuring principal ${PRINCIPAL_NAME}"
code=$(call GET "/api/management/v1/principals/${PRINCIPAL_NAME}")
if [ "$code" = "404" ]; then
  payload=$(jq -n --arg name "$PRINCIPAL_NAME" '{principal: {name: $name, properties: {}}}')
  code=$(call POST "/api/management/v1/principals" "$payload")
  require_status "$code" 200 201
  client_id=$(jq -er '.credentials.clientId' "$BODY_FILE")
  client_secret=$(jq -er '.credentials.clientSecret' "$BODY_FILE")
elif [ "$code" = "200" ]; then
  existing_id=$(jq -er '.clientId' "$BODY_FILE")
  saved_id=""
  if [ -f /bootstrap/client.env ]; then
    saved_id=$(read_env_value POLARIS_CLIENT_ID /bootstrap/client.env)
  fi
  if [ -n "$saved_id" ] && [ "$saved_id" = "$existing_id" ]; then
    client_id=$existing_id
    client_secret=$(read_env_value POLARIS_CLIENT_SECRET /bootstrap/client.env)
    echo "principal exists; reusing saved credentials"
  else
    echo "principal exists without saved credentials; recreating"
    code=$(call DELETE "/api/management/v1/principals/${PRINCIPAL_NAME}")
    require_status "$code" 200 202 204
    payload=$(jq -n --arg name "$PRINCIPAL_NAME" '{principal: {name: $name, properties: {}}}')
    code=$(call POST "/api/management/v1/principals" "$payload")
    require_status "$code" 200 201
    client_id=$(jq -er '.credentials.clientId' "$BODY_FILE")
    client_secret=$(jq -er '.credentials.clientSecret' "$BODY_FILE")
  fi
else
  require_status "$code" 200
fi

if [ -z "${client_secret}" ]; then
  echo "spark principal secret is empty" >&2
  exit 1
fi

echo "ensuring principal role ${PRINCIPAL_ROLE}"
payload=$(jq -n --arg name "$PRINCIPAL_ROLE" '{principalRole: {name: $name, properties: {}}}')
code=$(call POST "/api/management/v1/principal-roles" "$payload")
require_status "$code" 200 201 409

echo "ensuring catalog role ${CATALOG_ROLE}"
payload=$(jq -n --arg name "$CATALOG_ROLE" '{catalogRole: {name: $name, properties: {}}}')
code=$(call POST "/api/management/v1/catalogs/${catalog_path}/catalog-roles" "$payload")
require_status "$code" 200 201 409

echo "assigning principal role"
payload=$(jq -n --arg name "$PRINCIPAL_ROLE" '{principalRole: {name: $name}}')
code=$(call PUT "/api/management/v1/principals/${PRINCIPAL_NAME}/principal-roles" "$payload")
require_status "$code" 200 201 204 409

echo "assigning catalog role"
payload=$(jq -n --arg name "$CATALOG_ROLE" '{catalogRole: {name: $name}}')
code=$(call PUT "/api/management/v1/principal-roles/${PRINCIPAL_ROLE}/catalog-roles/${catalog_path}" "$payload")
require_status "$code" 200 201 204 409

echo "granting CATALOG_MANAGE_CONTENT"
payload='{"type":"catalog","privilege":"CATALOG_MANAGE_CONTENT"}'
code=$(call PUT "/api/management/v1/catalogs/${catalog_path}/catalog-roles/${CATALOG_ROLE}/grants" "$payload")
require_status "$code" 200 201 204 409

mkdir -p /bootstrap
tmp_file=$(mktemp)
{
  printf 'POLARIS_CLIENT_ID=%s\n' "$client_id"
  printf 'POLARIS_CLIENT_SECRET=%s\n' "$client_secret"
  printf 'POLARIS_CATALOG_NAME=%s\n' "$POLARIS_CATALOG_NAME"
  printf 'POLARIS_REALM=%s\n' "$POLARIS_REALM"
  printf 'OPENLINEAGE_NAMESPACE=%s\n' "$OPENLINEAGE_NAMESPACE"
} >"$tmp_file"
mv "$tmp_file" /bootstrap/client.env
chmod 644 /bootstrap/client.env

echo "catalog ${POLARIS_CATALOG_NAME} ready"
echo "spark principal client id ${client_id}"
echo "credentials written to .local/polaris/client.env"
