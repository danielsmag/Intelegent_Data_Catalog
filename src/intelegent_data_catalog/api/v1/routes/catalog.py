"""Catalog tree and table metadata routes."""

from typing import Annotated

from fastapi import APIRouter, Query

from intelegent_data_catalog.api.dependencies import (
    CatalogServiceDep,
    ExplorerServiceDep,
    QueryServiceDep,
)
from intelegent_data_catalog.catalog.models import (
    CatalogTree,
    ExplorerTree,
    QueryResult,
    TableDetail,
)

router: APIRouter = APIRouter()


@router.get("/explorer/tree", response_model=ExplorerTree)
def read_explorer_tree(service: ExplorerServiceDep) -> ExplorerTree:
    """Return Polaris and Trino, each with the catalogs it contains."""
    return service.tree()


@router.get(
    "/explorer/sources/{source_id}/tables/{catalog}/{namespace}/{table}",
    response_model=TableDetail,
)
def read_explorer_table(
    source_id: str,
    catalog: str,
    namespace: str,
    table: str,
    service: ExplorerServiceDep,
) -> TableDetail:
    """Return metadata for one table in a catalog source."""
    return service.table(source_id, catalog, namespace, table)


@router.get(
    "/explorer/sources/{source_id}/tables/{catalog}/{namespace}/{table}/sample",
    response_model=QueryResult,
)
def read_explorer_sample(
    source_id: str,
    catalog: str,
    namespace: str,
    table: str,
    service: ExplorerServiceDep,
    limit: Annotated[int | None, Query(ge=1)] = None,
) -> QueryResult:
    """Return a limited row preview from the source that owns the table."""
    return service.sample(source_id, catalog, namespace, table, limit)


@router.get("/tree", response_model=CatalogTree)
def read_tree(service: CatalogServiceDep) -> CatalogTree:
    """Return catalogs, schemas, and tables."""
    return service.tree()


@router.get("/tables/{catalog}/{namespace}/{table}", response_model=TableDetail)
def read_table(
    catalog: str,
    namespace: str,
    table: str,
    service: CatalogServiceDep,
) -> TableDetail:
    """Return metadata for one table."""
    return service.table(catalog, namespace, table)


@router.get("/tables/{catalog}/{namespace}/{table}/sample", response_model=QueryResult)
def read_sample(
    catalog: str,
    namespace: str,
    table: str,
    service: QueryServiceDep,
    limit: Annotated[int | None, Query(ge=1)] = None,
) -> QueryResult:
    """Return a limited row preview of one table."""
    return service.sample(catalog, namespace, table, limit)
