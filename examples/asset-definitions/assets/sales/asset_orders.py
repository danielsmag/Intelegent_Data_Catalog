"""Example asset declaration. Upload under s3://<bucket>/<prefix>/assets/."""

from intelegent_data_catalog.assets.dsl import (
    Asset,
    IcebergTableOperator,
)

asset = Asset(
    key="orders",
    operator=IcebergTableOperator(
        source_key="polaris.sales.orders",
        data_source="Polaris",
        include_lineage=True,
    ),
    schedule="0 2 * * *",
)
