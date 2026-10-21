"""Catalog API composition container."""

import logging
from collections.abc import Iterator
from pathlib import Path

import httpx
from dependency_injector import containers, providers

from intelegent_data_catalog.auth.oidc import OidcVerifier
from intelegent_data_catalog.catalog.repositories.catalog import CatalogRepository
from intelegent_data_catalog.catalog.repositories.polaris import PolarisClient
from intelegent_data_catalog.catalog.repositories.polaris_browser import PolarisCatalogBrowser
from intelegent_data_catalog.catalog.repositories.spark_query import SparkQueryClient
from intelegent_data_catalog.catalog.repositories.trino_browser import TrinoCatalogBrowser
from intelegent_data_catalog.catalog.services.catalog_service import CatalogService
from intelegent_data_catalog.catalog.services.explorer_service import ExplorerService
from intelegent_data_catalog.catalog.services.query_service import QueryService
from intelegent_data_catalog.catalog.sql_guard import SqlGuard
from intelegent_data_catalog.core.logger import configure_logging
from intelegent_data_catalog.core.settings import (
    AppSettings,
    ObjectStoreSettings,
    load_settings,
    resolve_project_root,
)
from intelegent_data_catalog.notebooks.directory import KernelDirectory
from intelegent_data_catalog.notebooks.gateway import KernelGatewayClient
from intelegent_data_catalog.notebooks.tickets import KernelTickets
from intelegent_data_catalog.persistence.database import CatalogDatabase
from intelegent_data_catalog.sources.trino.client import TrinoClient
from intelegent_data_catalog.storage.object_store import ObjectStore, build_object_store


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


def build_catalog_service(repository: CatalogRepository) -> CatalogService:
    """Build the catalog service around a metadata repository."""
    return CatalogService(repository)


def build_sql_guard(settings: AppSettings) -> SqlGuard:
    """Build the read-only SQL guard from query limits."""
    return SqlGuard(
        default_catalog=settings.polaris.catalog,
        max_rows=settings.query.max_query_rows,
        max_sql_chars=settings.query.max_sql_chars,
    )


def build_spark_query_client(
    settings: AppSettings,
    http_client: httpx.Client,
    logger: logging.Logger,
) -> SparkQueryClient:
    """Build the Spark SQL client from application settings."""
    client_logger: logging.Logger = logger.getChild("spark")
    return SparkQueryClient(
        http_client=http_client,
        spark_url=settings.query.spark_url,
        timeout_seconds=settings.query.query_timeout_seconds,
        logger=client_logger,
    )


def build_query_service(
    spark: SparkQueryClient,
    guard: SqlGuard,
    settings: AppSettings,
) -> QueryService:
    """Build the query service around Spark and the SQL guard."""
    return QueryService(spark=spark, guard=guard, settings=settings.query)


def build_explorer_service(
    polaris: PolarisClient,
    query: QueryService,
    settings: AppSettings,
    logger: logging.Logger,
) -> ExplorerService:
    """Build the catalog browser for Polaris and, when configured, Trino."""
    trino: TrinoCatalogBrowser | None = None
    if settings.trino.enabled:
        client: TrinoClient = TrinoClient(
            host=settings.trino.host,
            port=settings.trino.port,
            user=settings.trino.user,
            timeout_seconds=settings.trino.timeout_seconds,
        )
        trino = TrinoCatalogBrowser(client)
    explorer_logger: logging.Logger = logger.getChild("explorer")
    return ExplorerService(
        polaris=PolarisCatalogBrowser(polaris, query),
        trino=trino,
        sample_limit=settings.query.sample_limit,
        max_sample_limit=settings.query.max_sample_limit,
        logger=explorer_logger,
    )


def build_database(settings: AppSettings) -> CatalogDatabase:
    """Build the application catalog database from settings."""
    return CatalogDatabase(settings.database.url)


def build_kernel_gateway(settings: AppSettings) -> KernelGatewayClient:
    """Open an HTTP client for the notebook kernel gateway."""
    timeout: httpx.Timeout = httpx.Timeout(
        settings.notebook.execute_timeout_seconds,
        connect=10.0,
    )
    client: httpx.Client = httpx.Client(timeout=timeout)
    return KernelGatewayClient(
        http=client,
        base_url=settings.notebook.gateway_url,
        token=settings.notebook.gateway_token.get_secret_value(),
        kernel_name=settings.notebook.kernel_name,
    )


def build_asset_definition_store(settings: AppSettings) -> ObjectStore:
    """Object store for asset definition files. An empty bucket uses the workspace bucket."""
    chosen: str = settings.asset_definitions.bucket.strip()
    store_settings: ObjectStoreSettings = settings.object_store
    if chosen != "" and chosen != store_settings.bucket:
        store_settings = store_settings.model_copy(update={"bucket": chosen})
    return build_object_store(store_settings)


def build_kernel_tickets(settings: AppSettings) -> KernelTickets:
    """Build ticket signing from the notebook settings."""
    return KernelTickets(
        secret=settings.notebook.ticket_secret.get_secret_value(),
        ttl_seconds=settings.notebook.ticket_ttl_seconds,
    )


class AppContainer(containers.DeclarativeContainer):
    """Wires settings, logging, Polaris, Spark, and the catalog services."""

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
    sql_guard: providers.Singleton[SqlGuard] = providers.Singleton(
        build_sql_guard,
        settings,
    )
    spark_query_client: providers.Singleton[SparkQueryClient] = providers.Singleton(
        build_spark_query_client,
        settings,
        http_client,
        logger,
    )
    query_service: providers.Singleton[QueryService] = providers.Singleton(
        build_query_service,
        spark_query_client,
        sql_guard,
        settings,
    )
    explorer_service: providers.Singleton[ExplorerService] = providers.Singleton(
        build_explorer_service,
        polaris_client,
        query_service,
        settings,
        logger,
    )
    database: providers.Singleton[CatalogDatabase] = providers.Singleton(
        build_database,
        settings,
    )
    oidc_verifier: providers.Singleton[OidcVerifier] = providers.Singleton(
        OidcVerifier,
        settings.provided.auth,
    )
    object_store: providers.Singleton[ObjectStore] = providers.Singleton(
        build_object_store,
        settings.provided.object_store,
    )
    asset_definition_store: providers.Singleton[ObjectStore] = providers.Singleton(
        build_asset_definition_store,
        settings,
    )
    kernel_gateway: providers.Singleton[KernelGatewayClient] = providers.Singleton(
        build_kernel_gateway,
        settings,
    )
    kernel_tickets: providers.Singleton[KernelTickets] = providers.Singleton(
        build_kernel_tickets,
        settings,
    )
    kernel_directory: providers.Singleton[KernelDirectory] = providers.Singleton(KernelDirectory)
