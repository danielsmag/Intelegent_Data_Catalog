"""API composition root."""

import logging
from collections.abc import Iterator
from pathlib import Path

import httpx
from dependency_injector import containers, providers

from intelegent_data_catalog.catalog.polaris import PolarisClient
from intelegent_data_catalog.catalog.reader import CatalogReader
from intelegent_data_catalog.catalog.service import CatalogService
from intelegent_data_catalog.core.logger import configure_logging
from intelegent_data_catalog.core.settings import (
    AppSettings,
    load_settings,
    resolve_project_root,
)


def open_http_client(settings: AppSettings) -> Iterator[httpx.Client]:
    """Yield an HTTP client and close it on shutdown."""
    client: httpx.Client = httpx.Client(timeout=settings.polaris.timeout_seconds)
    yield client
    client.close()


def build_polaris_client(
    settings: AppSettings,
    http_client: httpx.Client,
    logger: logging.Logger,
) -> PolarisClient:
    """Build a Polaris client from application settings."""
    client_logger: logging.Logger = logger.getChild("polaris")
    return PolarisClient(
        settings=settings.polaris,
        http_client=http_client,
        logger=client_logger,
    )


def build_catalog_service(reader: CatalogReader) -> CatalogService:
    """Build the catalog service around a metadata reader."""
    return CatalogService(reader)


class ApiContainer(containers.DeclarativeContainer):
    """Wires settings, logging, the Polaris client, and the catalog service."""

    settings: providers.Singleton[AppSettings] = providers.Singleton(load_settings)
    project_root: providers.Singleton[Path] = providers.Singleton(resolve_project_root)
    logger: providers.Singleton[logging.Logger] = providers.Singleton(
        configure_logging,
        settings.provided.app.debug,
        settings.provided.logging,
        project_root,
    )
    http_client: providers.Resource[httpx.Client] = providers.Resource(
        open_http_client,
        settings,
    )
    polaris_client: providers.Singleton[PolarisClient] = providers.Singleton(
        build_polaris_client,
        settings,
        http_client,
        logger,
    )
    catalog_service: providers.Singleton[CatalogService] = providers.Singleton(
        build_catalog_service,
        polaris_client,
    )
