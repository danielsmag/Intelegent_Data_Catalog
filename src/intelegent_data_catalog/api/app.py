"""FastAPI application for the catalog explorer."""

import logging
from collections.abc import Awaitable, Callable

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import Response

from intelegent_data_catalog.api.exception_handlers import register_exception_handlers
from intelegent_data_catalog.api.v1.router import router as v1_router
from intelegent_data_catalog.core.logger import LOGGER_NAME

DEV_ORIGINS: list[str] = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]


def configure_app(app: FastAPI) -> None:
    """Attach CORS, access logs, error handlers, and the v1 router."""
    app.add_middleware(
        CORSMiddleware,
        allow_origins=DEV_ORIGINS,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["*"],
    )
    register_exception_handlers(app)
    app.include_router(v1_router)
    _add_access_log(app)


def _add_access_log(app: FastAPI) -> None:
    @app.middleware("http")
    async def log_request(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response: Response = await call_next(request)
        logger: logging.Logger = logging.getLogger(LOGGER_NAME)
        logger.info(
            "request method=%s path=%s status=%s",
            request.method,
            request.url.path,
            response.status_code,
        )
        return response
