#!/bin/sh
# Create the Iceberg warehouse bucket. Safe to run again.

set -eu

if [ -z "${WAREHOUSE_BUCKET:-}" ]; then
  echo "WAREHOUSE_BUCKET is required" >&2
  exit 1
fi

if aws s3api head-bucket --bucket "${WAREHOUSE_BUCKET}" >/dev/null 2>&1; then
  echo "bucket ${WAREHOUSE_BUCKET} already exists"
else
  aws s3 mb "s3://${WAREHOUSE_BUCKET}"
fi

aws s3 ls
