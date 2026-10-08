"""API process entrypoint."""

import logging
import sys

import uvicorn
from fastapi import FastAPI

from intelegent_data_catalog.api.access_log import uvicorn_log_config
from intelegent_data_catalog.api.container import AppContainer
from intelegent_data_catalog.bootstrap import create_app
from intelegent_data_catalog.core.settings import AppSettings
from intelegent_data_catalog.exceptions import CatalogError, PersistenceUnavailableError
from intelegent_data_catalog.persistence.database import CatalogDatabase
from intelegent_data_catalog.persistence.migrate import upgrade_database
from intelegent_data_catalog.registry.seed_lake import project_seed_lake
from intelegent_data_catalog.sources.trino.client import TrinoClient
from intelegent_data_catalog.sources.trino.discovery import TrinoCatalogSource


def main() -> None:
    """Serve the catalog API."""
    container: AppContainer = AppContainer()
    try:
        settings: AppSettings = container.settings()
        if settings.database.auto_migrate:
            upgrade_database(settings.database.url)
        logger: logging.Logger = container.logger()
        database: CatalogDatabase = container.database()
        _project_seed_lake(settings, database, logger)
        app: FastAPI = create_app(container)
    except PersistenceUnavailableError as err:
        print(f"startup failed: {err}", file=sys.stderr)
        raise SystemExit(1) from None
    except CatalogError as err:
        print(f"startup failed: {err}", file=sys.stderr)
        raise SystemExit(1) from err
    uvicorn.run(
        app,
        host=settings.api.host,
        port=settings.api.port,
        log_config=uvicorn_log_config(),
    )


def _project_seed_lake(
    settings: AppSettings,
    database: CatalogDatabase,
    logger: logging.Logger,
) -> None:
    """Copy seeded Iceberg tables into the registry before the API accepts traffic."""
    if not settings.trino.enabled:
        return
    source: TrinoCatalogSource = TrinoCatalogSource(
        TrinoClient(
            host=settings.trino.host,
            port=settings.trino.port,
            user=settings.trino.user,
            timeout_seconds=settings.trino.timeout_seconds,
        )
    )
    project_seed_lake(database, settings, source, logger)


if __name__ == "__main__":
    main()
