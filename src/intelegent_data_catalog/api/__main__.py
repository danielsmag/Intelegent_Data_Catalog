"""API process entrypoint."""

import sys

import uvicorn
from fastapi import FastAPI

from intelegent_data_catalog.bootstrap import create_app
from intelegent_data_catalog.core.container import AppContainer
from intelegent_data_catalog.core.settings import AppSettings
from intelegent_data_catalog.exceptions import CatalogError


def main() -> None:
    """Serve the catalog API."""
    container: AppContainer = AppContainer()
    try:
        settings: AppSettings = container.settings()
        app: FastAPI = create_app(container)
    except CatalogError as err:
        print(f"startup failed: {err}", file=sys.stderr)
        raise SystemExit(1) from err
    uvicorn.run(app, host=settings.api.host, port=settings.api.port)


if __name__ == "__main__":
    main()
