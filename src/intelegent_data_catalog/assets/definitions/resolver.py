"""Resolve operator references to catalog rows. Missing rows stay unresolved."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from intelegent_data_catalog.assets.definitions.models import (
    AirflowSourceRef,
    PageSourceRef,
    TableSourceRef,
)
from intelegent_data_catalog.persistence.enums import WorkspaceNodeKind
from intelegent_data_catalog.persistence.models import (
    AirflowDagRow,
    AirflowInstanceRow,
    CatalogTableRow,
    DataSourceRow,
    WorkspaceNodeRow,
    WorkspaceRow,
)


class UnresolvedSource:
    """The named source is not in the catalog yet."""

    def __init__(self, reason: str) -> None:
        self.reason: str = reason


class RejectedSource:
    """The named source cannot become an asset."""

    def __init__(self, reason: str) -> None:
        self.reason: str = reason


ResolvedSource = CatalogTableRow | AirflowDagRow | WorkspaceNodeRow
SourceOutcome = ResolvedSource | UnresolvedSource | RejectedSource


class SourceResolver:
    """Looks up tables, DAGs, and workspace files by the slugs a definition file used."""

    def __init__(self, session: Session) -> None:
        self._session: Session = session

    def resolve(self, source: TableSourceRef | AirflowSourceRef | PageSourceRef) -> SourceOutcome:
        """Return the source row, or why it cannot be linked yet."""
        match source:
            case TableSourceRef():
                return self._table(source)
            case AirflowSourceRef():
                return self._dag(source)
            case PageSourceRef():
                return self._page(source)

    def _table(self, source: TableSourceRef) -> CatalogTableRow | UnresolvedSource | RejectedSource:
        statement = select(CatalogTableRow).where(CatalogTableRow.source_key == source.source_key)
        if source.data_source is not None:
            source_ids: list[UUID] = list(
                self._session.scalars(
                    select(DataSourceRow.id).where(DataSourceRow.name == source.data_source),
                ),
            )
            if len(source_ids) == 0:
                return UnresolvedSource(f"data source not found: {source.data_source}")
            statement = statement.where(CatalogTableRow.data_source_id.in_(source_ids))
        rows: list[CatalogTableRow] = list(self._session.scalars(statement))
        if len(rows) == 0:
            return UnresolvedSource(f"table not found: {source.source_key}")
        if len(rows) > 1:
            return RejectedSource(f"source_key matches more than one table: {source.source_key}")
        return rows[0]

    def _dag(self, source: AirflowSourceRef) -> AirflowDagRow | UnresolvedSource:
        instance: AirflowInstanceRow | None = self._session.scalar(
            select(AirflowInstanceRow).where(AirflowInstanceRow.slug == source.instance),
        )
        if instance is None:
            return UnresolvedSource(f"airflow instance not found: {source.instance}")
        dag: AirflowDagRow | None = self._session.scalar(
            select(AirflowDagRow).where(
                AirflowDagRow.instance_id == instance.id,
                AirflowDagRow.dag_id == source.dag_id,
            ),
        )
        if dag is None:
            return UnresolvedSource(f"airflow dag not found: {source.dag_id}")
        return dag

    def _page(self, source: PageSourceRef) -> WorkspaceNodeRow | UnresolvedSource | RejectedSource:
        workspace: WorkspaceRow | None = self._session.scalar(
            select(WorkspaceRow).where(WorkspaceRow.slug == source.workspace),
        )
        if workspace is None:
            return UnresolvedSource(f"workspace not found: {source.workspace}")
        node: WorkspaceNodeRow | None = self._session.scalar(
            select(WorkspaceNodeRow).where(
                WorkspaceNodeRow.workspace_id == workspace.id,
                WorkspaceNodeRow.path == source.path,
            ),
        )
        if node is None:
            return UnresolvedSource(f"workspace file not found: {source.path}")
        if node.kind != WorkspaceNodeKind.FILE.value:
            return RejectedSource("folders are not assets")
        return node
