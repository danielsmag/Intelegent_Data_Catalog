"""Example asset declarations. Upload under s3://<bucket>/<prefix>/assets/."""

from intelegent_data_catalog.assets.dsl import (
    AirflowOperator,
    Asset,
    PageOperator,
    TableOperator,
)

Asset(
    "orders",
    TableOperator(source_key="iceberg.sales.orders", data_source="lakehouse"),
    scope="analytics/finance",
    name="Orders",
    context="Fact table of orders.",
)
Asset(
    "etl_orders",
    AirflowOperator(instance="prod-airflow", dag_id="etl_orders"),
    scope="analytics/finance",
)
Asset(
    "sales_readme",
    PageOperator(workspace="shared", path="/docs/sales.md"),
    scope="analytics/finance",
)
Asset(
    "kpi_notebook",
    PageOperator(path="/notebooks/kpi.ipynb"),
    space="analytics",
)
