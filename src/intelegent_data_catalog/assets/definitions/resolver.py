"""Resolve an asset source. A missing remote stays unresolved so the next scan retries."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from intelegent_data_catalog.assets.definitions.models import (
    AirflowInstanceSourceRef,
    GitHubRepoSourceRef,
    IcebergCatalogSourceRef,
    IcebergSchemaSourceRef,
    IcebergTableSourceRef,
    SourceRef,
)
from intelegent_data_catalog.assets.dsl.operators.ports import GitHubReader, IcebergReader
from intelegent_data_catalog.exceptions import (
    GitHubNotFoundError,
    GitHubUnavailableError,
    PolarisError,
    TableNotFoundError,
)
from intelegent_data_catalog.persistence.models import AirflowInstanceRow


class UnresolvedSource:
    """The named source is not available yet."""

    def __init__(self, reason: str) -> None:
        self.reason: str = reason


class SourceResolver:
    """Checks that a declaration's source can be read."""

    def __init__(self, session: Session, iceberg: IcebergReader, github: GitHubReader) -> None:
        self._session: Session = session
        self._iceberg: IcebergReader = iceberg
        self._github: GitHubReader = github

    def resolve(self, source: SourceRef) -> UnresolvedSource | None:
        """Return a reason when the source is missing. ``None`` means it resolved."""
        match source:
            case AirflowInstanceSourceRef():
                return self._airflow(source)
            case IcebergTableSourceRef():
                return self._table(source)
            case IcebergSchemaSourceRef():
                return self._schema(source)
            case IcebergCatalogSourceRef():
                return self._catalog(source)
            case GitHubRepoSourceRef():
                return self._github_repo(source)

    def _airflow(self, source: AirflowInstanceSourceRef) -> UnresolvedSource | None:
        row: AirflowInstanceRow | None = self._session.scalar(
            select(AirflowInstanceRow).where(AirflowInstanceRow.slug == source.instance),
        )
        if row is None:
            return UnresolvedSource(f"Airflow instance not registered: {source.instance}")
        return None

    def _table(self, source: IcebergTableSourceRef) -> UnresolvedSource | None:
        try:
            self._iceberg.load_table_raw(source.catalog, source.namespace, source.table)
        except TableNotFoundError as err:
            return UnresolvedSource(str(err))
        except PolarisError as err:
            return UnresolvedSource(str(err))
        return None

    def _catalog(self, source: IcebergCatalogSourceRef) -> UnresolvedSource | None:
        try:
            self._iceberg.list_namespaces(source.catalog)
        except PolarisError as err:
            return UnresolvedSource(str(err))
        return None

    def _schema(self, source: IcebergSchemaSourceRef) -> UnresolvedSource | None:
        try:
            self._iceberg.list_table_names(source.catalog, source.namespace)
        except PolarisError as err:
            return UnresolvedSource(str(err))
        return None

    def _github_repo(self, source: GitHubRepoSourceRef) -> UnresolvedSource | None:
        try:
            self._github.repo(source.owner, source.repo)
        except GitHubNotFoundError as err:
            return UnresolvedSource(str(err))
        except GitHubUnavailableError as err:
            return UnresolvedSource(str(err))
        return None
