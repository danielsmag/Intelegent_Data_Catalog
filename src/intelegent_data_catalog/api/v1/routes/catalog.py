"""Catalog tree and table metadata routes."""

from typing import Annotated

from fastapi import APIRouter, Query

from intelegent_data_catalog.api.dependencies import CatalogServiceDep, QueryServiceDep
from intelegent_data_catalog.catalog.models import CatalogTree, QueryResult, TableDetail

router: APIRouter = APIRouter()


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
