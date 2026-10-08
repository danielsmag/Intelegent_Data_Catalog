"""Health access logs stay quiet unless the check fails."""

import logging
from typing import Any

from intelegent_data_catalog.api.access_log import (
    QuietHealthAccessFilter,
    should_log_access,
    uvicorn_log_config,
)


def _record(path: str, status_code: int) -> logging.LogRecord:
    record: logging.LogRecord = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:49740", "GET", path, "1.1", status_code),
        exc_info=None,
    )
    return record


def test_successful_health_access_is_dropped() -> None:
    """200 on /api/v1/health does not pass the uvicorn access filter."""
    access_filter: QuietHealthAccessFilter = QuietHealthAccessFilter()
    assert access_filter.filter(_record("/api/v1/health", 200)) is False
    assert should_log_access("/api/v1/health", 200) is False


def test_failed_health_access_is_kept() -> None:
    """Error status on the health route still logs."""
    access_filter: QuietHealthAccessFilter = QuietHealthAccessFilter()
    assert access_filter.filter(_record("/api/v1/health", 503)) is True
    assert should_log_access("/health", 500) is True


def test_other_requests_still_log() -> None:
    """Non-health routes keep their access line, including successes."""
    access_filter: QuietHealthAccessFilter = QuietHealthAccessFilter()
    assert access_filter.filter(_record("/api/v1/catalogs", 200)) is True


def test_uvicorn_config_attaches_the_filter() -> None:
    """Access handler includes the quiet-health filter."""
    config: dict[str, Any] = uvicorn_log_config()
    handlers: dict[str, Any] = config["handlers"]
    access_handler: dict[str, Any] = handlers["access"]
    filters: list[str] = access_handler["filters"]
    assert "quiet_health" in filters
