#!/bin/sh
# Create the Iceberg warehouse bucket. Safe to run again.

set -eu

if [ -z "${WAREHOUSE_BUCKET:-}" ]; then
  echo "WAREHOUSE_BUCKET is required" >&2
  exit 1
fi

create_bucket() {
  bucket="$1"
  if aws s3api head-bucket --bucket "${bucket}" >/dev/null 2>&1; then
    echo "bucket ${bucket} already exists"
  else
    aws s3 mb "s3://${bucket}"
  fi
}

create_bucket "${WAREHOUSE_BUCKET}"
create_bucket "${WORKSPACE_BUCKET:-workspace}"

aws s3 ls
