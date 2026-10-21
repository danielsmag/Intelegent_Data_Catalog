"""SQLAlchemy store for catalog records."""

import uuid
from datetime import datetime

from sqlalchemy import Select, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from intelegent_data_catalog.exceptions import ConflictError
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.models import (
    AssetEngineBindingRow,
    AssetMembershipRow,
    CatalogTableRow,
    ColumnRow,
    DataSourceRow,
    EngineConnectionRow,
    MarquezMappingRow,
    MetadataSyncRunRow,
    RelationshipColumnMappingRow,
    RelationshipRow,
    ScopeRow,
    SourceEngineBindingRow,
    SpaceRow,
    TableTagRow,
    TagRow,
    WorkspaceSpaceBindingRow,
)

_CONFLICT: str = "catalog record conflicts with an existing record"


class RegistryRepository:
    """Reads and writes catalog rows in one session."""

    def __init__(self, session: Session) -> None:
        self._session: Session = session

    @property
    def session(self) -> Session:
        """Session this repository writes."""
        return self._session

    def add_space(
        self,
        *,
        name: str,
        slug: str,
        description: str | None,
        isolation_mode: str,
    ) -> SpaceRow:
        """Insert a space."""
        now: datetime = utc_now()
        row: SpaceRow = SpaceRow(
            id=uuid.uuid4(),
            name=name,
            slug=slug,
            description=description,
            isolation_mode=isolation_mode,
            created_at=now,
            updated_at=now,
        )
        return self._add(row)

    def list_spaces(self, space_ids: set[uuid.UUID] | None = None) -> list[SpaceRow]:
        """Return spaces in name order. ``space_ids`` limits the set when set."""
        statement: Select[SpaceRow] = select(SpaceRow).order_by(SpaceRow.name)
        if space_ids is not None:
            if len(space_ids) == 0:
                return []
            statement = statement.where(SpaceRow.id.in_(space_ids))
        return list[SpaceRow](self._session.scalars(statement))

    def bindings_for_spaces(self, space_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[uuid.UUID]]:
        """Return workspace ids bound to each space."""
        found: dict[uuid.UUID, list[uuid.UUID]] = {space_id: [] for space_id in space_ids}
        if len(space_ids) == 0:
            return found
        rows: list[WorkspaceSpaceBindingRow] = list(
            self._session.scalars(
                select(WorkspaceSpaceBindingRow).where(
                    WorkspaceSpaceBindingRow.space_id.in_(space_ids)
                )
            )
        )
        row: WorkspaceSpaceBindingRow
        for row in rows:
            found.setdefault(row.space_id, []).append(row.workspace_id)
        return found

    def add_space_binding(self, workspace_id: uuid.UUID, space_id: uuid.UUID) -> None:
        """Bind a space to a workspace. A duplicate pair is ignored."""
        existing: WorkspaceSpaceBindingRow | None = self._session.scalar(
            select(WorkspaceSpaceBindingRow).where(
                WorkspaceSpaceBindingRow.workspace_id == workspace_id,
                WorkspaceSpaceBindingRow.space_id == space_id,
            )
        )
        if existing is not None:
            return
        now: datetime = utc_now()
        self._add(
            WorkspaceSpaceBindingRow(
                id=uuid.uuid4(),
                workspace_id=workspace_id,
                space_id=space_id,
                created_at=now,
            )
        )

    def count_space_bindings(self, space_id: uuid.UUID) -> int:
        """Count workspaces bound to one space."""
        statement: Select[int] = (
            select(func.count())
            .select_from(WorkspaceSpaceBindingRow)
            .where(WorkspaceSpaceBindingRow.space_id == space_id)
        )
        return self._count(statement)

    def get_space(self, space_id: uuid.UUID) -> SpaceRow | None:
        """Return one space or none."""
        return self._session.get(SpaceRow, space_id)

    def find_space_by_slug(self, slug: str) -> SpaceRow | None:
        """Return the space that owns a slug."""
        return self._session.scalar(select(SpaceRow).where(SpaceRow.slug == slug))

    def count_scopes(self, space_id: uuid.UUID) -> int:
        """Count scopes in a space."""
        statement: Select[int] = (
            select(func.count()).select_from(ScopeRow).where(ScopeRow.space_id == space_id)
        )
        return self._count(statement)

    def add_scope(
        self,
        *,
        space_id: uuid.UUID,
        name: str,
        slug: str,
        description: str | None,
    ) -> ScopeRow:
        """Insert a scope."""
        now: datetime = utc_now()
        row: ScopeRow = ScopeRow(
            id=uuid.uuid4(),
            space_id=space_id,
            name=name,
            slug=slug,
            description=description,
            created_at=now,
            updated_at=now,
        )
        return self._add(row)

    def list_scopes(self, space_id: uuid.UUID) -> list[ScopeRow]:
        """Return scopes in one space in name order."""
        statement: Select[ScopeRow] = (
            select(ScopeRow).where(ScopeRow.space_id == space_id).order_by(ScopeRow.name)
        )
        return list[ScopeRow](self._session.scalars(statement))

    def get_scope(self, scope_id: uuid.UUID) -> ScopeRow | None:
        """Return one scope or none."""
        return self._session.get(ScopeRow, scope_id)

    def find_scope_by_slug(self, space_id: uuid.UUID, slug: str) -> ScopeRow | None:
        """Return a scope slug inside one space."""
        statement: Select[ScopeRow] = select(ScopeRow).where(
            ScopeRow.space_id == space_id,
            ScopeRow.slug == slug,
        )
        return self._session.scalar(statement)

    def count_tables_in_scope(self, scope_id: uuid.UUID) -> int:
        """Count tables assigned to a scope, including membership links."""
        member_ids: Select[tuple[uuid.UUID]] = select(AssetMembershipRow.asset_id).where(
            AssetMembershipRow.scope_id == scope_id,
        )
        statement: Select[int] = (
            select(func.count())
            .select_from(CatalogTableRow)
            .where(
                or_(
                    CatalogTableRow.scope_id == scope_id,
                    CatalogTableRow.asset_id.in_(member_ids),
                ),
            )
        )
        return self._count(statement)

    def clear_default_scope(self, scope_id: uuid.UUID) -> None:
        """Unset this scope as the default on every source binding."""
        statement: Select[SourceEngineBindingRow] = select(SourceEngineBindingRow).where(
            SourceEngineBindingRow.default_scope_id == scope_id
        )
        now: datetime = utc_now()
        for row in self._session.scalars(statement):
            row.default_scope_id = None
            row.updated_at = now
        self._flush()

    def add_data_source(
        self,
        *,
        name: str,
        source_type: str,
        configuration: dict[str, object],
        credential_ref: str | None,
    ) -> DataSourceRow:
        """Insert a data source."""
        now: datetime = utc_now()
        row: DataSourceRow = DataSourceRow(
            id=uuid.uuid4(),
            name=name,
            type=source_type,
            configuration=configuration,
            credential_ref=credential_ref,
            created_at=now,
            updated_at=now,
        )
        return self._add(row)

    def list_data_sources(self) -> list[DataSourceRow]:
        """Return data sources in name order."""
        statement: Select[DataSourceRow] = select(DataSourceRow).order_by(DataSourceRow.name)
        return list[DataSourceRow](self._session.scalars(statement))

    def get_data_source(self, data_source_id: uuid.UUID) -> DataSourceRow | None:
        """Return one data source or none."""
        return self._session.get(DataSourceRow, data_source_id)

    def count_tables_for_source(self, data_source_id: uuid.UUID) -> int:
        """Count tables imported from a data source."""
        statement: Select[int] = (
            select(func.count())
            .select_from(CatalogTableRow)
            .where(CatalogTableRow.data_source_id == data_source_id)
        )
        return self._count(statement)

    def count_bindings_for_source(self, data_source_id: uuid.UUID) -> int:
        """Count source engine bindings for a data source."""
        statement: Select[int] = (
            select(func.count())
            .select_from(SourceEngineBindingRow)
            .where(SourceEngineBindingRow.data_source_id == data_source_id)
        )
        return self._count(statement)

    def add_table(self, row: CatalogTableRow) -> CatalogTableRow:
        """Insert a fully populated catalog table."""
        return self._add(row)

    def list_tables(self, scope_id: uuid.UUID) -> list[CatalogTableRow]:
        """Return tables in one scope in name order, including membership links."""
        member_ids: Select[tuple[uuid.UUID]] = select(AssetMembershipRow.asset_id).where(
            AssetMembershipRow.scope_id == scope_id,
        )
        statement: Select[CatalogTableRow] = (
            select(CatalogTableRow)
            .where(
                or_(
                    CatalogTableRow.scope_id == scope_id,
                    CatalogTableRow.asset_id.in_(member_ids),
                ),
            )
            .order_by(CatalogTableRow.name)
        )
        return list[CatalogTableRow](self._session.scalars(statement))

    def get_table(self, table_id: uuid.UUID) -> CatalogTableRow | None:
        """Return one catalog table or none."""
        return self._session.get(CatalogTableRow, table_id)

    def find_table_by_source_key(
        self,
        data_source_id: uuid.UUID,
        source_key: str,
    ) -> CatalogTableRow | None:
        """Return the table for a case-sensitive source key."""
        statement: Select[CatalogTableRow] = select(CatalogTableRow).where(
            CatalogTableRow.data_source_id == data_source_id,
            CatalogTableRow.source_key == source_key,
        )
        return self._session.scalar(statement)

    def find_table_by_native_id(
        self,
        data_source_id: uuid.UUID,
        source_native_id: str,
    ) -> CatalogTableRow | None:
        """Return the table for a native id inside one data source."""
        statement: Select[CatalogTableRow] = select(CatalogTableRow).where(
            CatalogTableRow.data_source_id == data_source_id,
            CatalogTableRow.source_native_id == source_native_id,
        )
        return self._session.scalar(statement)

    def count_relationships_for_table(self, table_id: uuid.UUID) -> int:
        """Count relationships that start or end at a table."""
        statement: Select[int] = (
            select(func.count())
            .select_from(RelationshipRow)
            .where(
                (RelationshipRow.source_table_id == table_id)
                | (RelationshipRow.target_table_id == table_id)
            )
        )
        return self._count(statement)

    def delete_asset_bindings_for_table(self, table_id: uuid.UUID) -> None:
        """Remove asset bindings owned by a table."""
        self._session.execute(
            delete(AssetEngineBindingRow).where(AssetEngineBindingRow.catalog_table_id == table_id)
        )
        self._flush()

    def delete_columns_for_table(self, table_id: uuid.UUID) -> None:
        """Remove columns owned by a table."""
        self._session.execute(delete(ColumnRow).where(ColumnRow.table_id == table_id))
        self._flush()

    def add_column(self, row: ColumnRow) -> ColumnRow:
        """Insert a column."""
        return self._add(row)

    def list_columns(self, table_id: uuid.UUID) -> list[ColumnRow]:
        """Return columns in ordinal order."""
        statement: Select[ColumnRow] = (
            select(ColumnRow)
            .where(ColumnRow.table_id == table_id)
            .order_by(ColumnRow.ordinal_position, ColumnRow.name)
        )
        return list[ColumnRow](self._session.scalars(statement))

    def get_column(self, column_id: uuid.UUID) -> ColumnRow | None:
        """Return one column or none."""
        return self._session.get(ColumnRow, column_id)

    def find_column_by_name(self, table_id: uuid.UUID, name: str) -> ColumnRow | None:
        """Return a column by its case-sensitive name."""
        statement: Select[ColumnRow] = select(ColumnRow).where(
            ColumnRow.table_id == table_id,
            ColumnRow.name == name,
        )
        return self._session.scalar(statement)

    def count_mappings_for_column(self, column_id: uuid.UUID) -> int:
        """Count relationship mappings that use a column."""
        statement: Select[int] = (
            select(func.count())
            .select_from(RelationshipColumnMappingRow)
            .where(
                (RelationshipColumnMappingRow.source_column_id == column_id)
                | (RelationshipColumnMappingRow.target_column_id == column_id)
            )
        )
        return self._count(statement)

    def add_relationship(self, row: RelationshipRow) -> RelationshipRow:
        """Insert a relationship."""
        return self._add(row)

    def get_relationship(self, relationship_id: uuid.UUID) -> RelationshipRow | None:
        """Return one relationship or none."""
        return self._session.get(RelationshipRow, relationship_id)

    def list_relationships_between(self, table_ids: set[uuid.UUID]) -> list[RelationshipRow]:
        """Return relationships whose two tables are both in ``table_ids``."""
        if len(table_ids) == 0:
            return []
        statement: Select[RelationshipRow] = select(RelationshipRow).where(
            RelationshipRow.source_table_id.in_(table_ids),
            RelationshipRow.target_table_id.in_(table_ids),
        )
        return list[RelationshipRow](self._session.scalars(statement))

    def list_relationships_for_table(self, table_id: uuid.UUID) -> list[RelationshipRow]:
        """Return relationships that touch a table."""
        statement: Select[RelationshipRow] = (
            select(RelationshipRow)
            .where(
                (RelationshipRow.source_table_id == table_id)
                | (RelationshipRow.target_table_id == table_id)
            )
            .order_by(RelationshipRow.created_at)
        )
        return list[RelationshipRow](self._session.scalars(statement))

    def add_mapping(self, row: RelationshipColumnMappingRow) -> RelationshipColumnMappingRow:
        """Insert a relationship column mapping."""
        return self._add(row)

    def list_mappings(self, relationship_id: uuid.UUID) -> list[RelationshipColumnMappingRow]:
        """Return column mappings in position order."""
        statement: Select[RelationshipColumnMappingRow] = (
            select(RelationshipColumnMappingRow)
            .where(RelationshipColumnMappingRow.relationship_id == relationship_id)
            .order_by(RelationshipColumnMappingRow.position)
        )
        return list[RelationshipColumnMappingRow](self._session.scalars(statement))

    def delete_mappings(self, relationship_id: uuid.UUID) -> None:
        """Remove every column mapping on a relationship."""
        self._session.execute(
            delete(RelationshipColumnMappingRow).where(
                RelationshipColumnMappingRow.relationship_id == relationship_id
            )
        )
        self._flush()

    def add_engine_connection(self, row: EngineConnectionRow) -> EngineConnectionRow:
        """Insert an engine connection."""
        return self._add(row)

    def list_engine_connections(self) -> list[EngineConnectionRow]:
        """Return engine connections in name order."""
        statement: Select[EngineConnectionRow] = select(EngineConnectionRow).order_by(
            EngineConnectionRow.name
        )
        return list[EngineConnectionRow](self._session.scalars(statement))

    def get_engine_connection(self, engine_connection_id: uuid.UUID) -> EngineConnectionRow | None:
        """Return one engine connection or none."""
        return self._session.get(EngineConnectionRow, engine_connection_id)

    def count_source_bindings_for_engine(self, engine_connection_id: uuid.UUID) -> int:
        """Count source bindings on an engine connection."""
        statement: Select[int] = (
            select(func.count())
            .select_from(SourceEngineBindingRow)
            .where(SourceEngineBindingRow.engine_connection_id == engine_connection_id)
        )
        return self._count(statement)

    def add_source_binding(self, row: SourceEngineBindingRow) -> SourceEngineBindingRow:
        """Insert a source engine binding."""
        return self._add(row)

    def list_source_bindings(
        self,
        *,
        data_source_id: uuid.UUID | None,
        engine_connection_id: uuid.UUID | None,
    ) -> list[SourceEngineBindingRow]:
        """Return source bindings, optionally filtered."""
        statement: Select[SourceEngineBindingRow] = select(SourceEngineBindingRow)
        if data_source_id is not None:
            statement = statement.where(SourceEngineBindingRow.data_source_id == data_source_id)
        if engine_connection_id is not None:
            statement = statement.where(
                SourceEngineBindingRow.engine_connection_id == engine_connection_id
            )
        statement = statement.order_by(SourceEngineBindingRow.binding_key)
        return list[SourceEngineBindingRow](self._session.scalars(statement))

    def get_source_binding(self, source_binding_id: uuid.UUID) -> SourceEngineBindingRow | None:
        """Return one source binding or none."""
        return self._session.get(SourceEngineBindingRow, source_binding_id)

    def find_source_binding_by_key(
        self,
        engine_connection_id: uuid.UUID,
        binding_key: str,
    ) -> SourceEngineBindingRow | None:
        """Return a binding key on one engine connection."""
        statement: Select[SourceEngineBindingRow] = select(SourceEngineBindingRow).where(
            SourceEngineBindingRow.engine_connection_id == engine_connection_id,
            SourceEngineBindingRow.binding_key == binding_key,
        )
        return self._session.scalar(statement)

    def count_asset_bindings_for_source(self, source_binding_id: uuid.UUID) -> int:
        """Count asset bindings that use a source binding."""
        statement: Select[int] = (
            select(func.count())
            .select_from(AssetEngineBindingRow)
            .where(AssetEngineBindingRow.source_engine_binding_id == source_binding_id)
        )
        return self._count(statement)

    def count_sync_runs(self, source_binding_id: uuid.UUID) -> int:
        """Count sync runs for a source binding."""
        statement: Select[int] = (
            select(func.count())
            .select_from(MetadataSyncRunRow)
            .where(MetadataSyncRunRow.source_engine_binding_id == source_binding_id)
        )
        return self._count(statement)

    def add_asset_binding(self, row: AssetEngineBindingRow) -> AssetEngineBindingRow:
        """Insert an asset engine binding."""
        return self._add(row)

    def list_asset_bindings(self, table_id: uuid.UUID) -> list[AssetEngineBindingRow]:
        """Return asset bindings for a table."""
        statement: Select[AssetEngineBindingRow] = (
            select(AssetEngineBindingRow)
            .where(AssetEngineBindingRow.catalog_table_id == table_id)
            .order_by(AssetEngineBindingRow.role, AssetEngineBindingRow.engine_ref_key)
        )
        return list[AssetEngineBindingRow](self._session.scalars(statement))

    def get_asset_binding(self, binding_id: uuid.UUID) -> AssetEngineBindingRow | None:
        """Return one asset binding or none."""
        return self._session.get(AssetEngineBindingRow, binding_id)

    def find_asset_binding_by_key(
        self,
        source_binding_id: uuid.UUID,
        engine_ref_key: str,
    ) -> AssetEngineBindingRow | None:
        """Return an engine ref key on one source binding."""
        statement: Select[AssetEngineBindingRow] = select(AssetEngineBindingRow).where(
            AssetEngineBindingRow.source_engine_binding_id == source_binding_id,
            AssetEngineBindingRow.engine_ref_key == engine_ref_key,
        )
        return self._session.scalar(statement)

    def find_selected_query(self, table_id: uuid.UUID) -> AssetEngineBindingRow | None:
        """Return the selected QUERY binding for a table, if one is stored."""
        statement: Select[AssetEngineBindingRow] = select(AssetEngineBindingRow).where(
            AssetEngineBindingRow.catalog_table_id == table_id,
            AssetEngineBindingRow.role == "QUERY",
            AssetEngineBindingRow.is_selected.is_(True),
        )
        return self._session.scalar(statement)

    def add_sync_run(self, row: MetadataSyncRunRow) -> MetadataSyncRunRow:
        """Insert a metadata sync run."""
        return self._add(row)

    def list_sync_runs(self, source_binding_id: uuid.UUID | None) -> list[MetadataSyncRunRow]:
        """Return sync runs, newest start first."""
        statement: Select[MetadataSyncRunRow] = select(MetadataSyncRunRow)
        if source_binding_id is not None:
            statement = statement.where(
                MetadataSyncRunRow.source_engine_binding_id == source_binding_id
            )
        statement = statement.order_by(MetadataSyncRunRow.started_at.desc())
        return list[MetadataSyncRunRow](self._session.scalars(statement))

    def get_sync_run(self, sync_run_id: uuid.UUID) -> MetadataSyncRunRow | None:
        """Return one sync run or none."""
        return self._session.get(MetadataSyncRunRow, sync_run_id)

    def list_asset_bindings_for_source(
        self,
        source_binding_id: uuid.UUID,
    ) -> list[AssetEngineBindingRow]:
        """Return asset bindings that use one source binding."""
        statement: Select[AssetEngineBindingRow] = select(AssetEngineBindingRow).where(
            AssetEngineBindingRow.source_engine_binding_id == source_binding_id
        )
        return list[AssetEngineBindingRow](self._session.scalars(statement))

    def list_relationships_for_column(self, column_id: uuid.UUID) -> list[RelationshipRow]:
        """Return relationships whose mappings use a column."""
        mapping_ids: Select[uuid.UUID] = select(
            RelationshipColumnMappingRow.relationship_id
        ).where(
            (RelationshipColumnMappingRow.source_column_id == column_id)
            | (RelationshipColumnMappingRow.target_column_id == column_id)
        )
        statement: Select[RelationshipRow] = select(RelationshipRow).where(
            RelationshipRow.id.in_(mapping_ids)
        )
        return list[RelationshipRow](self._session.scalars(statement))

    def list_all_spaces(self) -> list[SpaceRow]:
        """Return every space."""
        return list[SpaceRow](self._session.scalars(select(SpaceRow).order_by(SpaceRow.name)))

    def list_all_scopes(self) -> list[ScopeRow]:
        """Return every scope."""
        return list[ScopeRow](self._session.scalars(select(ScopeRow).order_by(ScopeRow.name)))

    def list_all_tables(self) -> list[CatalogTableRow]:
        """Return every catalog table."""
        statement: Select[CatalogTableRow] = select(CatalogTableRow).order_by(
            CatalogTableRow.name
        )
        return list[CatalogTableRow](self._session.scalars(statement))

    def list_all_columns(self) -> list[ColumnRow]:
        """Return every column."""
        return list[ColumnRow](self._session.scalars(select(ColumnRow).order_by(ColumnRow.name)))

    def list_all_relationships(self) -> list[RelationshipRow]:
        """Return every relationship."""
        return list[RelationshipRow](self._session.scalars(select(RelationshipRow)))

    def list_all_mappings(self) -> list[RelationshipColumnMappingRow]:
        """Return every relationship column mapping."""
        statement: Select[RelationshipColumnMappingRow] = select(
            RelationshipColumnMappingRow
        )
        return list[RelationshipColumnMappingRow](self._session.scalars(statement))

    def list_tags(self) -> list[TagRow]:
        """Return tags in name order."""
        return list[TagRow](self._session.scalars(select(TagRow).order_by(TagRow.name)))

    def get_tag(self, tag_id: uuid.UUID) -> TagRow | None:
        """Return one tag or none."""
        return self._session.get(TagRow, tag_id)

    def find_tag_by_name(self, name: str) -> TagRow | None:
        """Return a tag by its case-sensitive name."""
        return self._session.scalar(select(TagRow).where(TagRow.name == name))

    def list_table_tags(self, table_id: uuid.UUID) -> list[TableTagRow]:
        """Return tags attached to one table."""
        statement: Select[TableTagRow] = select(TableTagRow).where(
            TableTagRow.table_id == table_id
        )
        return list[TableTagRow](self._session.scalars(statement))

    def find_table_tag(self, table_id: uuid.UUID, tag_id: uuid.UUID) -> TableTagRow | None:
        """Return one table-tag attachment."""
        statement: Select[TableTagRow] = select(TableTagRow).where(
            TableTagRow.table_id == table_id,
            TableTagRow.tag_id == tag_id,
        )
        return self._session.scalar(statement)

    def list_marquez_mappings(self) -> list[MarquezMappingRow]:
        """Return every Marquez dataset mapping."""
        return list[MarquezMappingRow](self._session.scalars(select(MarquezMappingRow)))

    def find_marquez_mapping_for_table(self, table_id: uuid.UUID) -> MarquezMappingRow | None:
        """Return the mapping for one catalog table."""
        statement: Select[MarquezMappingRow] = select(MarquezMappingRow).where(
            MarquezMappingRow.catalog_table_id == table_id
        )
        return self._session.scalar(statement)

    def find_marquez_mapping_for_dataset(
        self,
        namespace: str,
        dataset_name: str,
    ) -> MarquezMappingRow | None:
        """Return the mapping for one Marquez dataset."""
        statement: Select[MarquezMappingRow] = select(MarquezMappingRow).where(
            MarquezMappingRow.marquez_namespace == namespace,
            MarquezMappingRow.marquez_dataset_name == dataset_name,
        )
        return self._session.scalar(statement)

    def search_spaces(
        self,
        pattern: str,
        limit: int,
        space_ids: set[uuid.UUID] | None = None,
    ) -> list[SpaceRow]:
        """Search space names and descriptions."""
        if space_ids is not None and len(space_ids) == 0:
            return []
        statement: Select[SpaceRow] = select(SpaceRow).where(
            SpaceRow.name.ilike(pattern, escape="\\")
            | SpaceRow.slug.ilike(pattern, escape="\\")
            | SpaceRow.description.ilike(pattern, escape="\\")
        )
        if space_ids is not None:
            statement = statement.where(SpaceRow.id.in_(space_ids))
        return list[SpaceRow](self._session.scalars(statement.limit(limit)))

    def search_scopes(
        self,
        pattern: str,
        limit: int,
        space_ids: set[uuid.UUID] | None = None,
    ) -> list[ScopeRow]:
        """Search scope names and descriptions."""
        if space_ids is not None and len(space_ids) == 0:
            return []
        statement: Select[ScopeRow] = select(ScopeRow).where(
            ScopeRow.name.ilike(pattern, escape="\\")
            | ScopeRow.slug.ilike(pattern, escape="\\")
            | ScopeRow.description.ilike(pattern, escape="\\")
        )
        if space_ids is not None:
            statement = statement.where(ScopeRow.space_id.in_(space_ids))
        return list[ScopeRow](self._session.scalars(statement.limit(limit)))

    def search_tables(
        self,
        pattern: str,
        limit: int,
        space_ids: set[uuid.UUID] | None = None,
    ) -> list[CatalogTableRow]:
        """Search table names and both description fields."""
        if space_ids is not None and len(space_ids) == 0:
            return []
        statement: Select[CatalogTableRow] = select(CatalogTableRow).where(
            CatalogTableRow.name.ilike(pattern, escape="\\")
            | CatalogTableRow.display_name.ilike(pattern, escape="\\")
            | CatalogTableRow.description.ilike(pattern, escape="\\")
            | CatalogTableRow.source_description.ilike(pattern, escape="\\")
        )
        if space_ids is not None:
            statement = statement.join(ScopeRow, CatalogTableRow.scope_id == ScopeRow.id).where(
                ScopeRow.space_id.in_(space_ids)
            )
        return list[CatalogTableRow](self._session.scalars(statement.limit(limit)))

    def search_columns(
        self,
        pattern: str,
        limit: int,
        space_ids: set[uuid.UUID] | None = None,
    ) -> list[ColumnRow]:
        """Search column names and both description fields."""
        if space_ids is not None and len(space_ids) == 0:
            return []
        statement: Select[ColumnRow] = select(ColumnRow).where(
            ColumnRow.name.ilike(pattern, escape="\\")
            | ColumnRow.description.ilike(pattern, escape="\\")
            | ColumnRow.source_description.ilike(pattern, escape="\\")
        )
        if space_ids is not None:
            statement = (
                statement.join(CatalogTableRow, ColumnRow.table_id == CatalogTableRow.id)
                .join(ScopeRow, CatalogTableRow.scope_id == ScopeRow.id)
                .where(ScopeRow.space_id.in_(space_ids))
            )
        return list[ColumnRow](self._session.scalars(statement.limit(limit)))

    def search_tags(self, pattern: str, limit: int) -> list[TagRow]:
        """Search tag names and descriptions."""
        statement: Select[TagRow] = (
            select(TagRow)
            .where(
                TagRow.name.ilike(pattern, escape="\\")
                | TagRow.description.ilike(pattern, escape="\\")
            )
            .limit(limit)
        )
        return list[TagRow](self._session.scalars(statement))

    def delete(self, row: object) -> None:
        """Delete a row already loaded in this session."""
        self._session.delete(row)
        self._flush()

    def commit(self) -> None:
        """Commit the current transaction."""
        try:
            self._session.commit()
        except IntegrityError as err:
            self._session.rollback()
            raise ConflictError(_CONFLICT) from err

    def _add[RowT](self, row: RowT) -> RowT:
        self._session.add(row)
        self._flush()
        return row

    def _flush(self) -> None:
        try:
            self._session.flush()
        except IntegrityError as err:
            self._session.rollback()
            raise ConflictError(_CONFLICT) from err

    def _count(self, statement: Select[int]) -> int:
        counted: int | None = self._session.scalar(statement)
        if counted is None:
            return 0
        return counted
