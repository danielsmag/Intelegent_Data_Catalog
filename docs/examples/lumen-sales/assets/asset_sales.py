"""Deterministic assets. Each file declares one asset and the steps that run on it."""

from intelegent_data_catalog.assets.dsl import (
    AirflowInstance,
    AirflowMetadata,
    Asset,
    GitHubRepo,
    GitHubRepoSnapshot,
    IcebergSchema,
    IcebergSchemaMetadata,
    IcebergSchemaProfile,
    IcebergTable,
    IcebergTableMetadata,
    IcebergTableProfile,
)

sales = Asset(
    key="sales_orders",
    description="Iceberg metadata and a profile for the orders table.",
    source=IcebergTable(catalog="catalog", namespace="sales", table="orders"),
    operators=[IcebergTableMetadata(), IcebergTableProfile()],
    schedule="0 3 * * *",
)

schema = Asset(
    key="sales_schema",
    description="Metadata for every table in sales, plus a profile.",
    source=IcebergSchema(catalog="catalog", namespace="sales"),
    operators=[IcebergSchemaMetadata(), IcebergSchemaProfile(max_tables=10)],
    schedule="30 3 * * *",
)

airflow = Asset(
    key="prod_airflow",
    description="Full metadata for the production Airflow instance.",
    source=AirflowInstance(instance="prod-airflow"),
    operators=[AirflowMetadata()],
    schedule="0 * * * *",
)

repo = Asset(
    key="catalog_repo",
    description="Markdown snapshot of the catalog repository. A new master commit reruns it.",
    source=GitHubRepo(owner="example", repo="catalog", branch="master"),
    operators=[GitHubRepoSnapshot(include_readme=True)],
    schedule="*/15 * * * *",
)
