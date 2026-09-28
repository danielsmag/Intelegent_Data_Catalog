"""Read-only SQL route."""

from fastapi import APIRouter

from intelegent_data_catalog.api.dependencies import QueryServiceDep
from intelegent_data_catalog.api.v1.schemas import QueryRequest
from intelegent_data_catalog.catalog.models import QueryResult

router: APIRouter = APIRouter()


@router.post("/query", response_model=QueryResult)
def run_query(body: QueryRequest, service: QueryServiceDep) -> QueryResult:
    """Run one read-only Spark SQL statement."""
    return service.query(body.sql)
