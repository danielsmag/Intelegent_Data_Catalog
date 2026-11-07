"""Readers an operator calls. Production clients satisfy these structurally."""

from typing import Protocol

from pydantic import JsonValue

from intelegent_data_catalog.catalog.models import QueryResult
from intelegent_data_catalog.sources.airflow.models import (
    AirflowAsset,
    AirflowDag,
    AirflowDagRun,
    AirflowDagSource,
    AirflowDagStats,
    AirflowHealth,
    AirflowImportError,
    AirflowPlugin,
    AirflowPool,
    AirflowProvider,
    AirflowTask,
    AirflowVersion,
)
from intelegent_data_catalog.sources.github.models import GitHubCommit, GitHubRepository, GitHubTree


class AirflowReader(Protocol):
    """Airflow metadata used by ``AirflowMetadata``."""

    def version(self) -> AirflowVersion:
        """Read the Airflow version."""

    def health(self) -> AirflowHealth:
        """Read monitor health."""

    def list_dags(self) -> list[AirflowDag]:
        """Return every DAG."""

    def dag_tasks(self, dag_id: str) -> list[AirflowTask]:
        """Return tasks for one DAG."""

    def dag_runs(self, dag_id: str, limit: int) -> list[AirflowDagRun]:
        """Return the newest runs for one DAG."""

    def dag_source(self, dag_id: str) -> AirflowDagSource:
        """Return parsed source for one DAG."""

    def dag_stats(self, dag_ids: list[str]) -> list[AirflowDagStats]:
        """Return run counts."""

    def assets(self) -> list[AirflowAsset]:
        """Return Airflow assets."""

    def pools(self) -> list[AirflowPool]:
        """Return pools."""

    def providers(self) -> list[AirflowProvider]:
        """Return providers."""

    def plugins(self) -> list[AirflowPlugin]:
        """Return plugins."""

    def import_errors(self) -> list[AirflowImportError]:
        """Return import errors."""


class IcebergReader(Protocol):
    """Polaris reads used by the Iceberg operators."""

    def list_table_names(self, catalog: str, namespace: str) -> list[str]:
        """Return table names in one namespace."""

    def load_table_raw(self, catalog: str, namespace: str, table: str) -> dict[str, JsonValue]:
        """Return the full load-table document."""


class TableQuery(Protocol):
    """Read-only SQL used for profiling."""

    def query(self, sql: str) -> QueryResult:
        """Run one read-only statement."""


class GitHubReader(Protocol):
    """GitHub reads used by ``GitHubRepoSnapshot``."""

    def repo(self, owner: str, name: str) -> GitHubRepository:
        """Return repository metadata."""

    def branch_head(self, owner: str, name: str, branch: str) -> GitHubCommit:
        """Return the commit at ``branch``."""

    def tree(self, owner: str, name: str, sha: str) -> GitHubTree:
        """Return the recursive tree."""

    def file_text(self, owner: str, name: str, path: str, ref: str) -> str | None:
        """Return UTF-8 file text, or none when the file cannot be shown."""
