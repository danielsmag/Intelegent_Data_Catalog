"""Application composition container."""

import logging
from collections.abc import Iterator
from pathlib import Path

import httpx
from dependency_injector import containers, providers

from intelegent_data_catalog.catalog.repositories.catalog import CatalogRepository
from intelegent_data_catalog.catalog.repositories.polaris import PolarisClient
from intelegent_data_catalog.catalog.repositories.spark_query import SparkQueryClient
from intelegent_data_catalog.catalog.services.catalog_service import CatalogService
from intelegent_data_catalog.catalog.services.query_service import QueryService
from intelegent_data_catalog.catalog.sql_guard import SqlGuard
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
