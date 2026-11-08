"""Postgres checkpoints for durable graph runs. Tables belong to LangGraph."""

import logging
from contextlib import AbstractContextManager
from typing import cast

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres import PostgresSaver

from intelegent_data_catalog.core.logger import LOGGER_NAME

logger: logging.Logger = logging.getLogger(f"{LOGGER_NAME}.llm")

_SAVER: BaseCheckpointSaver[str] | None = None
_OPEN: AbstractContextManager[PostgresSaver] | None = None


def psycopg_url(database_url: str) -> str:
    """Return a psycopg URL. SQLAlchemy's driver suffix is removed."""
    prefix: str = "postgresql+psycopg://"
    if database_url.startswith(prefix):
        return f"postgresql://{database_url[len(prefix):]}"
    return database_url


def install_postgres_checkpointer(database_url: str) -> None:
    """Open a process-wide saver and create LangGraph's checkpoint tables."""
    global _SAVER, _OPEN
    if _SAVER is not None:
        return
    if not database_url.startswith("postgresql"):
        logger.info("skipping graph checkpoints for a non-postgres catalog database")
        return
    opened: object = PostgresSaver.from_conn_string(psycopg_url(database_url))
    manager: AbstractContextManager[PostgresSaver] = cast(
        AbstractContextManager[PostgresSaver],
        opened,
    )
    saver: PostgresSaver = manager.__enter__()
    saver.setup()
    _OPEN = manager
    _SAVER = saver


def active_checkpointer() -> BaseCheckpointSaver[str] | None:
    """Return the saver installed at startup. ``None`` when durable runs are off."""
    return _SAVER
