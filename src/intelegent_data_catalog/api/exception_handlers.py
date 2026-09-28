"""Map catalog errors to HTTP status codes."""

from collections.abc import Callable

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.requests import Request

from intelegent_data_catalog.api.v1.schemas import ErrorResponse
from intelegent_data_catalog.exceptions import (
    CatalogError,
    PolarisError,
    QueryNotFoundError,
    QueryRejectedError,
    QueryTimeoutError,
    SparkQueryError,
    TableNotFoundError,
)

_STATUS_BY_ERROR: dict[type[CatalogError], int] = {
    TableNotFoundError: 404,
    QueryNotFoundError: 404,
    QueryRejectedError: 400,
    QueryTimeoutError: 504,
    SparkQueryError: 502,
    PolarisError: 502,
}


def register_exception_handlers(app: FastAPI) -> None:
    """Register one handler per catalog error that the API exposes."""
    error_type: type[CatalogError]
    status_code: int
    for error_type, status_code in _STATUS_BY_ERROR.items():
        app.add_exception_handler(error_type, _handler(status_code))


def _handler(status_code: int) -> Callable[[Request, Exception], JSONResponse]:
    def handle(_request: Request, err: Exception) -> JSONResponse:
        body: ErrorResponse = ErrorResponse(detail=str(err))
        return JSONResponse(status_code=status_code, content=body.model_dump())

    return handle
