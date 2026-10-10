"""Append rows to Iceberg tables that the catalog stack already created."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import date
from decimal import Decimal

import pyarrow as pa
from pyiceberg.catalog import Catalog, load_catalog
from pyiceberg.exceptions import CommitFailedException, NoSuchTableError, RESTError
from pyiceberg.table import Table

from iceberg_target.errors import IcebergWriteError
from iceberg_target.settings import IcebergSettings

logger: logging.Logger = logging.getLogger("iceberg_target")


class IcebergTableWriter:
    """Opens the Polaris REST catalog and appends one row to an existing table."""

    def __init__(self, settings: IcebergSettings) -> None:
        self._settings: IcebergSettings = settings

    @classmethod
    def from_env(cls) -> IcebergTableWriter:
        """Build a writer from the Airflow container environment."""
        return cls(IcebergSettings.from_env())

    def append_customer(self) -> int:
        """Append one customer to ``sales.customers``. Returns the new customer id."""
        catalog: Catalog = self._open_catalog()
        identifier: str = self._identifier("customers")
        table: Table = self._load(catalog, identifier)
        customer_id: int = self._next_id(table, "customer_id", identifier)
        row: dict[str, int | str | date | Decimal] = {
            "customer_id": customer_id,
            "name": f"Airflow Customer {customer_id}",
            "email": f"airflow.customer.{customer_id}@example.com",
            "city": "Test",
        }
        self._append_row(table, row, identifier)
        logger.info("appended %s customer_id=%s", identifier, customer_id)
        return customer_id

    def append_order(self) -> int:
        """Append one order to ``sales.orders``. Returns the new order id.

        ``customer_id`` is an id already stored in ``sales.customers``.
        """
        catalog: Catalog = self._open_catalog()
        customers_name: str = self._identifier("customers")
        orders_name: str = self._identifier("orders")
        customers: Table = self._load(catalog, customers_name)
        customer_id: int = self._lowest_id(customers, "customer_id", customers_name)
        orders: Table = self._load(catalog, orders_name)
        order_id: int = self._next_id(orders, "order_id", orders_name)
        row: dict[str, int | str | date | Decimal] = {
            "order_id": order_id,
            "customer_id": customer_id,
            "order_date": date.today(),
            "status": "placed",
            "amount": Decimal("12.50"),
        }
        self._append_row(orders, row, orders_name)
        logger.info("appended %s order_id=%s", orders_name, order_id)
        return order_id

    def _identifier(self, table_name: str) -> str:
        return f"{self._settings.namespace}.{table_name}"

    def _open_catalog(self) -> Catalog:
        properties: dict[str, str] = {
            "type": "rest",
            "uri": self._settings.polaris_uri,
            "oauth2-server-uri": self._settings.polaris_token_uri,
            "credential": f"{self._settings.client_id}:{self._settings.client_secret}",
            "warehouse": self._settings.catalog_name,
            "scope": "PRINCIPAL_ROLE:ALL",
            "header.Polaris-Realm": self._settings.realm,
            "header.X-Iceberg-Access-Delegation": "vended-credentials",
            "token-refresh-enabled": "true",
            "py-io-impl": "pyiceberg.io.fsspec.FsspecFileIO",
            "s3.endpoint": self._settings.s3_endpoint,
            "s3.path-style-access": "true",
            "s3.region": self._settings.s3_region,
        }
        catalog: Catalog = load_catalog("polaris", **properties)
        return catalog

    def _load(self, catalog: Catalog, identifier: str) -> Table:
        try:
            table: Table = catalog.load_table(identifier)
        except NoSuchTableError as err:
            raise IcebergWriteError(
                identifier,
                "table does not exist; start the catalog stack so the seed tables are present",
            ) from err
        except RESTError as err:
            raise IcebergWriteError(identifier, str(err)) from err
        return table

    def _append_row(
        self,
        table: Table,
        row: Mapping[str, int | str | date | Decimal],
        identifier: str,
    ) -> None:
        pending: pa.Table = pa.Table.from_pylist([dict(row)], schema=table.schema().as_arrow())
        try:
            table.append(pending)
        except (CommitFailedException, RESTError, OSError, ValueError, pa.ArrowInvalid) as err:
            raise IcebergWriteError(identifier, str(err)) from err

    def _next_id(self, table: Table, column_name: str, identifier: str) -> int:
        present: list[int] = self._column_values(table, column_name, identifier)
        if not present:
            return 1
        return max(present) + 1

    def _lowest_id(self, table: Table, column_name: str, identifier: str) -> int:
        present: list[int] = self._column_values(table, column_name, identifier)
        if not present:
            raise IcebergWriteError(identifier, "table has no rows to reference")
        return min(present)

    def _column_values(self, table: Table, column_name: str, identifier: str) -> list[int]:
        try:
            scanned: pa.Table = table.scan(selected_fields=(column_name,)).to_arrow()
        except (RESTError, OSError, ValueError) as err:
            raise IcebergWriteError(identifier, str(err)) from err
        if scanned.num_rows == 0:
            return []
        raw_values: list[int | None] = scanned.column(column_name).to_pylist()
        present: list[int] = [int(value) for value in raw_values if value is not None]
        return present
