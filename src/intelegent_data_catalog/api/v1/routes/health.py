"""Process health route."""

from fastapi import APIRouter

from intelegent_data_catalog.api.v1.schemas import HealthResponse

router: APIRouter = APIRouter()


@router.get("/health", response_model=HealthResponse)
def read_health() -> HealthResponse:
    """Report that the API process is up."""
    return HealthResponse(status="ok")
