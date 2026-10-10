"""Append one row to the existing Polaris table sales.orders."""

from datetime import UTC, datetime

from airflow.sdk import DAG, dag, task
from iceberg_target.writer import IcebergTableWriter


@dag(
    dag_id="append_sales_order",
    description="Append one row to the existing Iceberg table sales.orders.",
    schedule=None,
    start_date=datetime(2026, 1, 1, tzinfo=UTC),
    catchup=False,
    max_active_runs=1,
    is_paused_upon_creation=False,
    default_args={"retries": 0},
    tags=["iceberg", "polaris"],
)
def append_sales_order() -> None:
    """Write one order for a customer id that already exists. Does not create the table."""

    @task
    def write_order() -> int:
        writer: IcebergTableWriter = IcebergTableWriter.from_env()
        order_id: int = writer.append_order()
        return order_id

    write_order()


append_sales_order_dag: DAG = append_sales_order()
