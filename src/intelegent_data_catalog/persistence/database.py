"""Catalog database engine and sessions."""

from typing import Protocol, cast

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


class _Cursor(Protocol):
    """DB-API cursor used to enable SQLite foreign keys."""

    def execute(self, statement: str) -> object:
        """Run one statement."""

    def close(self) -> None:
        """Release the cursor."""


class _DbapiConnection(Protocol):
    """DB-API connection exposed by the SQLAlchemy connect event."""

    def cursor(self) -> _Cursor:
        """Return a cursor."""


class CatalogDatabase:
    """Lazy SQLAlchemy engine for the application catalog."""

    def __init__(self, url: str) -> None:
        self.url: str = url
        connect_args: dict[str, bool | float] = {}
        if url.startswith("sqlite"):
            connect_args = {"check_same_thread": False, "timeout": 30.0}
        self._engine: Engine = create_engine(url, connect_args=connect_args, pool_pre_ping=True)
        if url.startswith("sqlite"):
            _enable_sqlite_foreign_keys(self._engine)
        self._session_factory: sessionmaker[Session] = sessionmaker(
            bind=self._engine,
            autoflush=False,
            expire_on_commit=False,
        )

    def session(self) -> Session:
        """Open a session. The caller commits and closes it."""
        return self._session_factory()

    def dispose(self) -> None:
        """Close pooled connections."""
        self._engine.dispose()


def _enable_sqlite_foreign_keys(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _set_pragma(dbapi_connection: object, _connection_record: object) -> None:
        connection: _DbapiConnection = cast(_DbapiConnection, dbapi_connection)
        cursor: _Cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
