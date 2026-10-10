"""DAG to catalog table matching. MANUAL links are not produced by the matcher."""

from uuid import UUID, uuid4

from intelegent_data_catalog.airflow.mapping import (
    AssetView,
    DagView,
    MappingCandidate,
    TableView,
    match_dag_tables,
)
from intelegent_data_catalog.persistence.enums import (
    AirflowMappingDirection,
    AirflowMappingOrigin,
)


def test_asset_uri_sets_direction() -> None:
    """Producing tasks are outputs. Consuming and scheduled DAGs are inputs."""
    orders: TableView = _table("catalog", "sales", "orders")
    customers: TableView = _table("catalog", "sales", "customers")
    producer: DagView = DagView("append_sales_order", None, None)
    consumer: DagView = DagView("read_orders", None, None)
    scheduled: DagView = DagView("wait_customers", None, None)
    assets: list[AssetView] = [
        AssetView(
            uri="iceberg://catalog/sales/orders",
            producing_dag_ids=frozenset({"append_sales_order"}),
            consuming_dag_ids=frozenset({"read_orders"}),
            scheduled_dag_ids=frozenset(),
        ),
        AssetView(
            uri="iceberg://catalog/sales/customers",
            producing_dag_ids=frozenset(),
            consuming_dag_ids=frozenset(),
            scheduled_dag_ids=frozenset({"wait_customers"}),
        ),
    ]
    found: list[MappingCandidate] = match_dag_tables(
        [producer, consumer, scheduled],
        assets,
        [orders, customers],
    )
    by_dag: dict[str, MappingCandidate] = {item.dag_id: item for item in found}
    assert by_dag["append_sales_order"].direction is AirflowMappingDirection.OUTPUT
    assert by_dag["append_sales_order"].origin is AirflowMappingOrigin.ASSET
    assert by_dag["append_sales_order"].table_id == orders.table_id
    assert by_dag["append_sales_order"].evidence == "iceberg://catalog/sales/orders"
    assert by_dag["read_orders"].direction is AirflowMappingDirection.INPUT
    assert by_dag["wait_customers"].direction is AirflowMappingDirection.INPUT
    assert by_dag["wait_customers"].table_id == customers.table_id


def test_source_token_matches_sales_customers() -> None:
    """A word-bounded namespace.table token in source is an unknown link."""
    customers: TableView = _table("catalog", "sales", "customers")
    dag: DagView = DagView(
        "append_sales_customer",
        "nightly append",
        "target = 'sales.customers'\n",
    )
    found: list[MappingCandidate] = match_dag_tables([dag], [], [customers])
    assert len(found) == 1
    match: MappingCandidate = found[0]
    assert match.origin is AirflowMappingOrigin.SOURCE_MATCH
    assert match.direction is AirflowMappingDirection.UNKNOWN
    assert match.evidence == "sales.customers"
    assert match.table_id == customers.table_id


def test_source_token_allows_a_trailing_period() -> None:
    """Repo DAG text ends the table name with a sentence period."""
    customers: TableView = _table("catalog", "sales", "customers")
    dag: DagView = DagView(
        "append_sales_customer",
        "Append one row to the existing Iceberg table sales.customers.",
        None,
    )
    found: list[MappingCandidate] = match_dag_tables([dag], [], [customers])
    assert len(found) == 1
    assert found[0].evidence == "sales.customers"


def test_source_match_rejects_substrings() -> None:
    """customers_archive, a shorter name, and a prefixed name do not match."""
    customers: TableView = _table("catalog", "sales", "customers")
    customer: TableView = _table("catalog", "sales", "customer")
    archive: DagView = DagView(
        "append_sales_customer",
        None,
        "sales.customers_archive\nmysales.customers\nsales.customer\nsales.customers.extra\n",
    )
    assert match_dag_tables([archive], [], [customers]) == []
    longer: DagView = DagView("append_sales_customer", None, "sales.customers\n")
    found: list[MappingCandidate] = match_dag_tables([longer], [], [customer])
    assert found == []


def test_matcher_never_emits_manual() -> None:
    """Sync discovery cannot invent a MANUAL row."""
    table: TableView = _table("catalog", "sales", "customers")
    dag: DagView = DagView("append_sales_customer", None, "sales.customers")
    asset: AssetView = AssetView(
        uri="iceberg://catalog/sales/customers",
        producing_dag_ids=frozenset({"append_sales_customer"}),
        consuming_dag_ids=frozenset({"append_sales_customer"}),
        scheduled_dag_ids=frozenset(),
    )
    found: list[MappingCandidate] = match_dag_tables([dag], [asset], [table])
    origins: set[AirflowMappingOrigin] = {item.origin for item in found}
    assert AirflowMappingOrigin.MANUAL not in origins
    output: MappingCandidate = next(
        item for item in found if item.origin is AirflowMappingOrigin.ASSET
    )
    assert output.direction is AirflowMappingDirection.OUTPUT


def _table(catalog: str, namespace: str, table: str) -> TableView:
    table_id: UUID = uuid4()
    return TableView(table_id=table_id, catalog=catalog, namespace=namespace, table=table)
