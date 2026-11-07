"""Resolve operator references to catalog rows. Missing rows stay unresolved."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from intelegent_data_catalog.agents.definitions.models import (
    AirflowInstanceSourceRef,
    AirflowSourceRef,
    PageSourceRef,
    SchemaSourceRef,
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
    """The named source cannot become an agent."""

    def __init__(self, reason: str) -> None:
        self.reason: str = reason


ResolvedSource = CatalogTableRow | AirflowDagRow | AirflowInstanceRow | WorkspaceNodeRow
SourceOutcome = ResolvedSource | UnresolvedSource | RejectedSource
SchemaOutcome = list[CatalogTableRow] | UnresolvedSource


class SourceResolver:
    """Looks up tables, DAGs, and workspace files by the slugs a definition file used."""

    def __init__(self, session: Session) -> None:
        self._session: Session = session

    def resolve(
        self,
        source: (
            TableSourceRef
            | AirflowSourceRef
            | AirflowInstanceSourceRef
            | PageSourceRef
            | SchemaSourceRef
        ),
    ) -> SourceOutcome | SchemaOutcome:
        """Return the source row, or why it cannot be linked yet.

        A schema returns every matching table, or why none can be linked yet.
        """
        match source:
            case TableSourceRef():
                return self._table(source)
            case AirflowSourceRef():
                return self._dag(source)
            case AirflowInstanceSourceRef():
                return self._instance(source)
            case PageSourceRef():
                return self._page(source)
            case SchemaSourceRef():
                return self._schema(source)

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

    def _instance(self, source: AirflowInstanceSourceRef) -> AirflowInstanceRow | UnresolvedSource:
        instance: AirflowInstanceRow | None = self._session.scalar(
            select(AirflowInstanceRow).where(AirflowInstanceRow.slug == source.instance),
        )
        if instance is None:
            return UnresolvedSource(f"airflow instance not found: {source.instance}")
        return instance

    def _schema(self, source: SchemaSourceRef) -> SchemaOutcome:
        statement = select(CatalogTableRow).where(
            CatalogTableRow.physical_catalog == source.catalog,
            CatalogTableRow.physical_namespace == source.namespace,
        )
        if source.data_source is not None:
            source_ids: list[UUID] = list(
                self._session.scalars(
                    select(DataSourceRow.id).where(DataSourceRow.name == source.data_source),
                ),
            )
            if len(source_ids) == 0:
                return UnresolvedSource(f"data source not found: {source.data_source}")
            statement = statement.where(CatalogTableRow.data_source_id.in_(source_ids))
        if source.include:
            statement = statement.where(CatalogTableRow.name.in_(source.include))
        if source.exclude:
            statement = statement.where(CatalogTableRow.name.notin_(list(source.exclude)))
        rows: list[CatalogTableRow] = list(
            self._session.scalars(statement.order_by(CatalogTableRow.name)),
        )
        if len(rows) == 0:
            return UnresolvedSource(f"no tables in schema: {source.catalog}.{source.namespace}")
        return rows

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
            return RejectedSource("folders are not agents")
        return node
