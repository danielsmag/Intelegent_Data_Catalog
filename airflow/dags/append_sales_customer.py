"""Append one row to the existing Polaris table sales.customers."""

from datetime import UTC, datetime

from airflow.sdk import DAG, dag, task
from iceberg_target.writer import IcebergTableWriter


@dag(
    dag_id="append_sales_customer",
    description="Append one row to the existing Iceberg table sales.customers.",
    schedule=None,
    start_date=datetime(2026, 1, 1, tzinfo=UTC),
    catchup=False,
    max_active_runs=1,
    is_paused_upon_creation=False,
    default_args={"retries": 0},
    tags=["iceberg", "polaris"],
)
def append_sales_customer() -> None:
    """Write one Airflow-generated customer. Does not create the table."""

    @task
    def write_customer() -> int:
        writer: IcebergTableWriter = IcebergTableWriter.from_env()
        customer_id: int = writer.append_customer()
        return customer_id

    write_customer()



append_sales_customer_dag: DAG = append_sales_customer()
