"""Example asset declarations. Upload under s3://<bucket>/<prefix>/assets/."""

from intelegent_data_catalog.assets.dsl import (
    Asset,
    IcebergTableOperator,
)

Asset(
    "orders",
    IcebergTableOperator(source_key="iceberg.sales.orders", data_source="lakehouse"),
    scope="analytics/finance",
    name="Orders",
    context="Fact table of orders.",
)
