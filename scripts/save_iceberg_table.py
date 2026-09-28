"""Save local Iceberg tables with row data under the project db folder."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
from pydantic import BaseModel, Field
from pyiceberg.catalog import Catalog, load_catalog
from pyiceberg.exceptions import NoSuchTableError
from pyiceberg.table import Table

from intelegent_data_catalog.exceptions import CatalogError

NAMESPACE: str = "catalog"
CUSTOMERS_TABLE: str = f"{NAMESPACE}.customers"
ORDERS_TABLE: str = f"{NAMESPACE}.orders"
ASSET_CATALOG_TABLE: str = f"{NAMESPACE}.assets"


class IcebergSaveError(CatalogError):
    """Raised when a local Iceberg table cannot be written."""


class Customer(BaseModel):
    """One customer row."""

    customer_id: int = Field(ge=1)
    name: str = Field(min_length=1)
    email: str = Field(min_length=1)
    city: str = Field(min_length=1)


class Order(BaseModel):
    """One order row."""

    order_id: int = Field(ge=1)
    customer_id: int = Field(ge=1)
    order_date: date
    status: str = Field(min_length=1)
    amount: Decimal = Field(gt=0)


class LocalIcebergStore:
    """SQLite catalog and file warehouse under a db directory."""

    def __init__(self, db_dir: Path) -> None:
        self._db_dir: Path = db_dir

    def save(self, customers: list[Customer], orders: list[Order]) -> dict[str, int]:
        """Write customers and orders. Append only primary keys that are missing.

        Returns:
            Row count for each table after the save.
        """
        warehouse: Path = self._prepare_dirs()
        catalog: Catalog = self._open_catalog(warehouse)
        catalog.create_namespace_if_not_exists(NAMESPACE)
        self._purge_asset_catalog(catalog)
        customer_count: int = self._save_table(
            catalog,
            CUSTOMERS_TABLE,
            _customer_schema(),
            _customers_arrow(customers),
            "customer_id",
        )
        order_count: int = self._save_table(
            catalog,
            ORDERS_TABLE,
            _order_schema(),
            _orders_arrow(orders),
            "order_id",
        )
        counts: dict[str, int] = {
            CUSTOMERS_TABLE: customer_count,
            ORDERS_TABLE: order_count,
        }
        return counts

    def _save_table(
        self,
        catalog: Catalog,
        identifier: str,
        schema: pa.Schema,
        rows: pa.Table,
        key_column: str,
    ) -> int:
        table: Table = catalog.create_table_if_not_exists(identifier, schema=schema)
        existing: pa.Table = table.scan().to_arrow()
        pending: pa.Table = _missing_rows(rows, existing, key_column)
        if pending.num_rows > 0:
            table.append(pending)
            existing = table.scan().to_arrow()
        return existing.num_rows

    def _purge_asset_catalog(self, catalog: Catalog) -> None:
        try:
            catalog.purge_table(ASSET_CATALOG_TABLE)
        except NoSuchTableError:
            return

    def _prepare_dirs(self) -> Path:
        self._db_dir.mkdir(parents=True, exist_ok=True)
        warehouse: Path = self._db_dir / "warehouse"
        warehouse.mkdir(parents=True, exist_ok=True)
        return warehouse

    def _open_catalog(self, warehouse: Path) -> Catalog:
        catalog_db: Path = self._db_dir / "catalog.db"
        return load_catalog(
            "local",
            type="sql",
            uri=f"sqlite:///{catalog_db}",
            warehouse=f"file://{warehouse}",
        )


def _customer_schema() -> pa.Schema:
    return pa.schema(
        [
            pa.field("customer_id", pa.int64(), nullable=False),
            pa.field("name", pa.string(), nullable=False),
            pa.field("email", pa.string(), nullable=False),
            pa.field("city", pa.string(), nullable=False),
        ]
    )


def _order_schema() -> pa.Schema:
    return pa.schema(
        [
            pa.field("order_id", pa.int64(), nullable=False),
            pa.field("customer_id", pa.int64(), nullable=False),
            pa.field("order_date", pa.date32(), nullable=False),
            pa.field("status", pa.string(), nullable=False),
            pa.field("amount", pa.decimal128(10, 2), nullable=False),
        ]
    )


def _customers_arrow(customers: list[Customer]) -> pa.Table:
    rows: list[dict[str, int | str]] = [
        {
            "customer_id": customer.customer_id,
            "name": customer.name,
            "email": customer.email,
            "city": customer.city,
        }
        for customer in customers
    ]
    return pa.Table.from_pylist(rows, schema=_customer_schema())


def _orders_arrow(orders: list[Order]) -> pa.Table:
    rows: list[dict[str, int | date | str | Decimal]] = [
        {
            "order_id": order.order_id,
            "customer_id": order.customer_id,
            "order_date": order.order_date,
            "status": order.status,
            "amount": order.amount,
        }
        for order in orders
    ]
    return pa.Table.from_pylist(rows, schema=_order_schema())


def _missing_rows(incoming: pa.Table, existing: pa.Table, key_column: str) -> pa.Table:
    if existing.num_rows == 0:
        return incoming
    stored_keys: set[int] = {int(value) for value in existing.column(key_column).to_pylist()}
    keep_indexes: list[int] = []
    incoming_keys: list[int] = [int(value) for value in incoming.column(key_column).to_pylist()]
    for index, key in enumerate(incoming_keys):
        if key not in stored_keys:
            keep_indexes.append(index)
    if not keep_indexes:
        return incoming.slice(0, 0)
    return incoming.take(keep_indexes)


def project_db_dir() -> Path:
    """Return the db directory at the repository root."""
    return Path(__file__).resolve().parent.parent / "db"


def seed_customers() -> list[Customer]:
    """Return customer rows written into catalog.customers."""
    return [
        Customer(customer_id=1, name="Ada Lovelace", email="ada@example.com", city="London"),
        Customer(customer_id=2, name="Alan Turing", email="alan@example.com", city="Cambridge"),
        Customer(customer_id=3, name="Grace Hopper", email="grace@example.com", city="New York"),
        Customer(
            customer_id=4,
            name="Katherine Johnson",
            email="katherine@example.com",
            city="Hampton",
        ),
        Customer(
            customer_id=5,
            name="Margaret Hamilton",
            email="margaret@example.com",
            city="Cambridge",
        ),
    ]


def seed_orders() -> list[Order]:
    """Return order rows written into catalog.orders."""
    return [
        Order(
            order_id=1001,
            customer_id=1,
            order_date=date(2026, 1, 12),
            status="delivered",
            amount=Decimal("120.50"),
        ),
        Order(
            order_id=1002,
            customer_id=1,
            order_date=date(2026, 2, 3),
            status="shipped",
            amount=Decimal("45.00"),
        ),
        Order(
            order_id=1003,
            customer_id=2,
            order_date=date(2026, 2, 18),
            status="placed",
            amount=Decimal("89.99"),
        ),
        Order(
            order_id=1004,
            customer_id=3,
            order_date=date(2026, 3, 1),
            status="delivered",
            amount=Decimal("210.00"),
        ),
        Order(
            order_id=1005,
            customer_id=3,
            order_date=date(2026, 3, 15),
            status="cancelled",
            amount=Decimal("15.25"),
        ),
        Order(
            order_id=1006,
            customer_id=4,
            order_date=date(2026, 4, 2),
            status="shipped",
            amount=Decimal("64.40"),
        ),
        Order(
            order_id=1007,
            customer_id=5,
            order_date=date(2026, 4, 20),
            status="placed",
            amount=Decimal("300.00"),
        ),
        Order(
            order_id=1008,
            customer_id=2,
            order_date=date(2026, 5, 5),
            status="delivered",
            amount=Decimal("18.75"),
        ),
    ]


def main() -> None:
    """Write customer and order rows into the local db folder."""
    db_dir: Path = project_db_dir()
    store: LocalIcebergStore = LocalIcebergStore(db_dir)
    try:
        counts: dict[str, int] = store.save(seed_customers(), seed_orders())
    except CatalogError:
        raise
    except (OSError, ValueError) as err:
        raise IcebergSaveError(f"failed to save Iceberg tables in {db_dir}") from err
    for identifier, row_count in counts.items():
        print(f"saved {identifier} ({row_count} rows) under {db_dir}")


if __name__ == "__main__":
    main()
