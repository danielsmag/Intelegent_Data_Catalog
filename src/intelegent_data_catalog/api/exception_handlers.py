"""Map catalog errors to HTTP status codes."""

from collections.abc import Callable

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.requests import Request

from intelegent_data_catalog.api.v1.schemas import ErrorResponse
from intelegent_data_catalog.exceptions import (
    AirflowAuthError,
    AirflowUnavailableError,
    AuthenticationError,
    AuthorizationError,
    CatalogError,
    ConflictError,
    EntityNotFoundError,
    GraphUnavailableError,
    LlmError,
    LlmUnavailableError,
    NotebookDisabledError,
    NotebookGatewayError,
    PayloadTooLargeError,
    PersistenceUnavailableError,
    PolarisError,
    ProfileUnavailableError,
    QueryNotFoundError,
    QueryRejectedError,
    QueryTimeoutError,
    SparkQueryError,
    TableNotFoundError,
    TrinoUnavailableError,
    ValidationAppError,
)

_STATUS_BY_ERROR: dict[type[CatalogError], int] = {
    TableNotFoundError: 404,
    QueryNotFoundError: 404,
    EntityNotFoundError: 404,
    AuthenticationError: 401,
    AuthorizationError: 403,
    PayloadTooLargeError: 413,
    QueryRejectedError: 400,
    ValidationAppError: 422,
    ConflictError: 409,
    ProfileUnavailableError: 501,
    LlmUnavailableError: 501,
    LlmError: 502,
    QueryTimeoutError: 504,
    SparkQueryError: 502,
    PolarisError: 502,
    TrinoUnavailableError: 502,
    AirflowAuthError: 502,
    AirflowUnavailableError: 502,
    NotebookGatewayError: 502,
    GraphUnavailableError: 503,
    PersistenceUnavailableError: 503,
    NotebookDisabledError: 501,
}


def register_exception_handlers(app: FastAPI) -> None:
    """Register one handler per catalog error that the API exposes."""
    error_type: type[CatalogError]
    status_code: int
    for error_type, status_code in _STATUS_BY_ERROR.items():
        app.add_exception_handler(error_type, _handler(status_code))
    app.add_exception_handler(RequestValidationError, _validation_error)


def _handler(status_code: int) -> Callable[[Request, Exception], JSONResponse]:
    def handle(_request: Request, err: Exception) -> JSONResponse:
        body: ErrorResponse = ErrorResponse(detail=str(err))
        return JSONResponse(status_code=status_code, content=body.model_dump())

    return handle


def _validation_error(_request: Request, err: Exception) -> JSONResponse:
    """Return validation messages without echoing submitted secret values."""
    if not isinstance(err, RequestValidationError):
        body: ErrorResponse = ErrorResponse(detail="invalid request")
        return JSONResponse(status_code=422, content=body.model_dump())
    details: list[dict[str, object]] = []
    item: dict[str, object]
    for item in err.errors():
        location: object = item.get("loc", ())
        parts: list[str] = []
        if isinstance(location, tuple | list):
            part: object
            for part in location:
                parts.append(str(part))
        message: object = item.get("msg", "invalid request")
        kind: object = item.get("type", "value_error")
        details.append(
            {
                "type": kind if isinstance(kind, str) else "value_error",
                "loc": parts,
                "msg": message if isinstance(message, str) else "invalid request",
            }
        )
    return JSONResponse(status_code=422, content={"detail": details})
