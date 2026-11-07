"""Catalog-backed operator context."""

import logging
from uuid import UUID

import httpx
from pydantic import JsonValue, TypeAdapter
from sqlalchemy import select
from sqlalchemy.orm import Session

from intelegent_data_catalog.assets.definitions.models import (
    AirflowInstanceSourceRef,
    SourceRef,
)
from intelegent_data_catalog.assets.dsl.operators.base import OperatorContext
from intelegent_data_catalog.assets.dsl.operators.ports import (
    AirflowReader,
    GitHubReader,
    IcebergReader,
    TableQuery,
)
from intelegent_data_catalog.assets.execution.outputs import AssetOutputStore
from intelegent_data_catalog.core.settings import AirflowSettings
from intelegent_data_catalog.exceptions import CatalogError
from intelegent_data_catalog.persistence.models import AirflowInstanceRow, AssetRow
from intelegent_data_catalog.sources.airflow.client import AirflowClient

_SOURCE: TypeAdapter[SourceRef] = TypeAdapter(SourceRef)


class CatalogOperatorContext(OperatorContext):
    """Reads Airflow, Iceberg, and GitHub through the process clients."""

    def __init__(
        self,
        session: Session,
        asset: AssetRow,
        logger: logging.Logger,
        *,
        force: bool,
        outputs: AssetOutputStore,
        iceberg: IcebergReader,
        query: TableQuery,
        github: GitHubReader,
        http_client: httpx.Client,
        airflow_settings: AirflowSettings,
    ) -> None:
        super().__init__(asset.id, asset.key, logger, force=force)
        self._session: Session = session
        self._asset: AssetRow = asset
        self._outputs: AssetOutputStore = outputs
        self._iceberg: IcebergReader = iceberg
        self._query: TableQuery = query
        self._github: GitHubReader = github
        self._http: httpx.Client = http_client
        self._airflow_settings: AirflowSettings = airflow_settings
        self._airflow: AirflowClient | None = None
        self._source: SourceRef | None = None

    def source(self) -> SourceRef:
        """Return the source stored on the asset."""
        if self._source is None:
            self._source = _SOURCE.validate_python(self._asset.source)
        return self._source

    def head_sha(self) -> str | None:
        """Return the last snapshotted GitHub commit."""
        stored: object = self._asset.properties.get("head_sha")
        if isinstance(stored, str) and stored != "":
            return stored
        return None

    def remember_head(self, sha: str) -> None:
        """Keep ``sha`` on the asset so an unchanged branch can be skipped."""
        properties: dict[str, object] = dict(self._asset.properties)
        properties["head_sha"] = sha
        self._asset.properties = properties
        self._session.flush()

    def write_output(self, name: str, markdown: str, data: JsonValue | None) -> None:
        """Store markdown for the step ``set_task`` selected."""
        self._outputs.write(self._task_id, name, markdown, data)

    def airflow(self) -> AirflowReader:
        """Open a client for the asset's Airflow instance."""
        if self._airflow is not None:
            return self._airflow
        source: SourceRef = self.source()
        if not isinstance(source, AirflowInstanceSourceRef):
            raise CatalogError("asset source is not an Airflow instance")
        row: AirflowInstanceRow | None = self._session.scalar(
            select(AirflowInstanceRow).where(AirflowInstanceRow.slug == source.instance),
        )
        if row is None:
            raise CatalogError(f"Airflow instance not registered: {source.instance}")
        self._airflow = AirflowClient(
            base_url=row.base_url,
            username=row.username,
            password=row.password,
            timeout_seconds=self._airflow_settings.timeout_seconds,
            http_client=self._http,
            logger=self.logger,
        )
        return self._airflow

    def iceberg(self) -> IcebergReader:
        """Return the Polaris reader."""
        return self._iceberg

    def query(self) -> TableQuery:
        """Return the Spark query runner."""
        return self._query

    def github(self) -> GitHubReader:
        """Return the GitHub reader."""
        return self._github

    @property
    def asset_row_id(self) -> UUID:
        """Return the asset id. Present so callers do not reach into the row."""
        return self._asset.id
