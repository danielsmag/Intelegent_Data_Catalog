"""Access-log rules. Successful health probes stay quiet."""

import logging
from copy import deepcopy
from typing import Any

from uvicorn.config import LOGGING_CONFIG

_QUIET_HEALTH_PATHS: frozenset[str] = frozenset({"/health", "/api/v1/health"})
_ERROR_STATUS: int = 400
_PATH_ARG: int = 2
_STATUS_ARG: int = 4


class QuietHealthAccessFilter(logging.Filter):
    """Drop uvicorn access lines for healthy `/health` checks."""

    def filter(self, record: logging.LogRecord) -> bool:
        path: str | None
        status_code: int | None
        path, status_code = _path_and_status(record)
        if path is None or status_code is None:
            return True
        return should_log_access(path, status_code)


def should_log_access(path: str, status_code: int) -> bool:
    """True unless this is a successful health check."""
    normalized: str = path.split("?", 1)[0].rstrip("/") or "/"
    return not (normalized in _QUIET_HEALTH_PATHS and status_code < _ERROR_STATUS)


def uvicorn_log_config() -> dict[str, Any]:
    """Uvicorn logging config with the health-check filter on access logs."""
    config: dict[str, Any] = deepcopy(LOGGING_CONFIG)
    filters: dict[str, Any] = dict(config.get("filters", {}))
    filters["quiet_health"] = {"()": QuietHealthAccessFilter}
    config["filters"] = filters
    handlers: dict[str, Any] = config["handlers"]
    access_handler: dict[str, Any] = dict(handlers["access"])
    access_filters: list[str] = list(access_handler.get("filters", []))
    access_filters.append("quiet_health")
    access_handler["filters"] = access_filters
    handlers["access"] = access_handler
    return config


def _path_and_status(record: logging.LogRecord) -> tuple[str | None, int | None]:
    args: object = record.args
    if not isinstance(args, tuple) or len(args) <= _STATUS_ARG:
        return None, None
    path_value: object = args[_PATH_ARG]
    status_value: object = args[_STATUS_ARG]
    path: str | None = path_value if isinstance(path_value, str) else None
    status_code: int | None = status_value if isinstance(status_value, int) else None
    return path, status_code
