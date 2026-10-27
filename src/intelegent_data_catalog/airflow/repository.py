"""SQLAlchemy store for Airflow instances and DAG snapshots."""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Select, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import InstrumentedAttribute, Session
from sqlalchemy.sql.elements import ColumnElement

from intelegent_data_catalog.exceptions import ConflictError
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.enums import AirflowMappingOrigin, AssetStatus
from intelegent_data_catalog.persistence.models import (
    AirflowAttachmentRow,
    AirflowDagRow,
    AirflowDagTableRow,
    AirflowInstanceRow,
    AirflowSyncRunRow,
    AssetMembershipRow,
    AssetRow,
    CatalogTableRow,
)

_CONFLICT: str = "airflow record conflicts with an existing record"
_DISCOVERED: tuple[str, str] = (
    AirflowMappingOrigin.ASSET.value,
    AirflowMappingOrigin.SOURCE_MATCH.value,
)


@dataclass(frozen=True)
class DagWrite:
    """Fields applied to one DAG snapshot row."""

    dag_id: str
    display_name: str | None
    description: str | None
    is_paused: bool
    is_stale: bool
    has_import_errors: bool
    timetable_summary: str | None
    timetable_description: str | None
    owners: list[str]
    tags: list[str]
    fileloc: str | None
    bundle_name: str | None
    max_active_runs: int | None
    next_dagrun_run_after: datetime | None
    last_parsed_time: datetime | None
    tasks: list[dict[str, object]]
    last_run: dict[str, object] | None
    run_stats: list[dict[str, object]]
    source_code: str | None
    source_version: int | None


@dataclass(frozen=True)
class LlmMappingWrite:
    """LLM DAG-to-table link. Replacing these leaves MANUAL and discovered rows."""

    dag_row_id: uuid.UUID
    table_id: uuid.UUID
    direction: str
    evidence: str | None


@dataclass(frozen=True)
class AttachedDag:
    """One DAG reached through a space or scope attachment."""

    dag: AirflowDagRow
    instance: AirflowInstanceRow
    via: str
    attachment_id: uuid.UUID


@dataclass(frozen=True)
class MappingWrite:
    """Discovered DAG-to-table link. MANUAL is ignored by replace."""

    dag_id: str
    table_id: uuid.UUID
    origin: str
    direction: str
    evidence: str | None


class AirflowRepository:
    """Reads and writes Airflow rows in one session."""

    def __init__(self, session: Session) -> None:
        self._session: Session = session

    @property
    def session(self) -> Session:
        """Session this repository writes."""
        return self._session

    def add_instance(
        self,
        *,
        name: str,
        slug: str,
        base_url: str,
        username: str,
        password: str,
        verify_tls: bool,
        description: str | None,
        status: str,
    ) -> AirflowInstanceRow:
        """Insert an instance."""
        now: datetime = utc_now()
        row: AirflowInstanceRow = AirflowInstanceRow(
            id=uuid.uuid4(),
            name=name,
            slug=slug,
            base_url=base_url,
            username=username,
            password=password,
            verify_tls=verify_tls,
            description=description,
            status=status,
            last_synced_at=None,
            last_error=None,
            created_at=now,
            updated_at=now,
        )
        return self._add(row)

    def list_instances(self) -> list[AirflowInstanceRow]:
        """Return instances ordered by name."""
        statement: Select[tuple[AirflowInstanceRow]] = select(AirflowInstanceRow).order_by(
            AirflowInstanceRow.name,
        )
        return list(self._session.scalars(statement))

    def get_instance(self, instance_id: uuid.UUID) -> AirflowInstanceRow | None:
        """Return one instance."""
        return self._session.get(AirflowInstanceRow, instance_id)

    def find_instance_by_slug(self, slug: str) -> AirflowInstanceRow | None:
        """Return the instance with this slug."""
        statement: Select[tuple[AirflowInstanceRow]] = select(AirflowInstanceRow).where(
            AirflowInstanceRow.slug == slug,
        )
        return self._session.scalar(statement)

    def find_instance_by_base_url(self, base_url: str) -> AirflowInstanceRow | None:
        """Return the instance registered at this API URL."""
        statement: Select[tuple[AirflowInstanceRow]] = select(AirflowInstanceRow).where(
            AirflowInstanceRow.base_url == base_url,
        )
        return self._session.scalar(statement)

    def dag_counts(self) -> dict[uuid.UUID, int]:
        """Count synced DAGs per instance."""
        statement = (
            select(AirflowDagRow.instance_id, func.count())
            .group_by(AirflowDagRow.instance_id)
        )
        counts: dict[uuid.UUID, int] = {}
        for row in self._session.execute(statement):
            raw_id: object = row[0]
            raw_count: object = row[1]
            if isinstance(raw_id, uuid.UUID) and isinstance(raw_count, int):
                counts[raw_id] = raw_count
        return counts

    def upsert_dag(self, instance_id: uuid.UUID, write: DagWrite) -> AirflowDagRow:
        """Insert or update one DAG snapshot."""
        existing: AirflowDagRow | None = self.find_dag(instance_id, write.dag_id)
        if existing is None:
            row: AirflowDagRow = AirflowDagRow(
                id=uuid.uuid4(),
                instance_id=instance_id,
                dag_id=write.dag_id,
                display_name=write.display_name,
                description=write.description,
                is_paused=write.is_paused,
                is_stale=write.is_stale,
                has_import_errors=write.has_import_errors,
                timetable_summary=write.timetable_summary,
                timetable_description=write.timetable_description,
                owners=write.owners,
                tags=write.tags,
                fileloc=write.fileloc,
                bundle_name=write.bundle_name,
                max_active_runs=write.max_active_runs,
                next_dagrun_run_after=write.next_dagrun_run_after,
                last_parsed_time=write.last_parsed_time,
                tasks=write.tasks,
                last_run=write.last_run,
                run_stats=write.run_stats,
                source_code=write.source_code,
                source_version=write.source_version,
                last_seen_at=utc_now(),
            )
            return self._add(row)
        self._apply_dag(existing, write)
        return existing

    def find_dag(self, instance_id: uuid.UUID, dag_id: str) -> AirflowDagRow | None:
        """Return one DAG snapshot."""
        statement: Select[tuple[AirflowDagRow]] = select(AirflowDagRow).where(
            AirflowDagRow.instance_id == instance_id,
            AirflowDagRow.dag_id == dag_id,
        )
        return self._session.scalar(statement)

    def list_dags(self, instance_id: uuid.UUID) -> list[AirflowDagRow]:
        """Return DAG snapshots for one instance."""
        statement: Select[tuple[AirflowDagRow]] = (
            select(AirflowDagRow)
            .where(AirflowDagRow.instance_id == instance_id)
            .order_by(AirflowDagRow.dag_id)
        )
        return list(self._session.scalars(statement))

    def delete_missing_dags(self, instance_id: uuid.UUID, keep: set[str]) -> int:
        """Delete snapshots whose dag id was not in the latest list."""
        removed: int = 0
        row: AirflowDagRow
        for row in self.list_dags(instance_id):
            if row.dag_id in keep:
                continue
            if row.asset_id is not None:
                asset: AssetRow | None = self._session.get(AssetRow, row.asset_id)
                if asset is not None:
                    asset.status = AssetStatus.MISSING.value
                    asset.updated_at = utc_now()
            self._session.delete(row)
            removed += 1
        if removed > 0:
            self._flush()
        return removed

    def list_mappings_for_instance(self, instance_id: uuid.UUID) -> list[AirflowDagTableRow]:
        """Return every table link for DAGs of one instance."""
        statement: Select[tuple[AirflowDagTableRow]] = (
            select(AirflowDagTableRow)
            .join(AirflowDagRow, AirflowDagRow.id == AirflowDagTableRow.dag_row_id)
            .where(AirflowDagRow.instance_id == instance_id)
        )
        return list(self._session.scalars(statement))

    def list_links(self, dag_row_id: uuid.UUID, table_id: uuid.UUID) -> list[AirflowDagTableRow]:
        """Return links between one DAG row and one catalog table."""
        statement: Select[tuple[AirflowDagTableRow]] = select(AirflowDagTableRow).where(
            AirflowDagTableRow.dag_row_id == dag_row_id,
            AirflowDagTableRow.catalog_table_id == table_id,
        )
        return list(self._session.scalars(statement))

    def find_mapping(
        self,
        dag_row_id: uuid.UUID,
        table_id: uuid.UUID,
        origin: str,
    ) -> AirflowDagTableRow | None:
        """Return one origin-specific link."""
        statement: Select[tuple[AirflowDagTableRow]] = select(AirflowDagTableRow).where(
            AirflowDagTableRow.dag_row_id == dag_row_id,
            AirflowDagTableRow.catalog_table_id == table_id,
            AirflowDagTableRow.origin == origin,
        )
        return self._session.scalar(statement)

    def add_mapping(
        self,
        *,
        dag_row_id: uuid.UUID,
        table_id: uuid.UUID,
        origin: str,
        direction: str,
        evidence: str | None,
    ) -> AirflowDagTableRow:
        """Insert one DAG-to-table link."""
        row: AirflowDagTableRow = AirflowDagTableRow(
            id=uuid.uuid4(),
            dag_row_id=dag_row_id,
            catalog_table_id=table_id,
            origin=origin,
            direction=direction,
            evidence=evidence,
        )
        return self._add(row)

    def replace_discovered_mappings(
        self,
        instance_id: uuid.UUID,
        writes: list[MappingWrite],
    ) -> int:
        """Replace ASSET and SOURCE_MATCH links. MANUAL rows stay."""
        dags: dict[str, AirflowDagRow] = {
            row.dag_id: row for row in self.list_dags(instance_id)
        }
        dag_row_ids: list[uuid.UUID] = [row.id for row in dags.values()]
        if len(dag_row_ids) > 0:
            self._session.execute(
                delete(AirflowDagTableRow).where(
                    AirflowDagTableRow.dag_row_id.in_(dag_row_ids),
                    AirflowDagTableRow.origin.in_(_DISCOVERED),
                ),
            )
            self._flush()
        inserted: int = 0
        write: MappingWrite
        for write in writes:
            if write.origin == AirflowMappingOrigin.MANUAL.value:
                continue
            dag: AirflowDagRow | None = dags.get(write.dag_id)
            if dag is None:
                continue
            self.add_mapping(
                dag_row_id=dag.id,
                table_id=write.table_id,
                origin=write.origin,
                direction=write.direction,
                evidence=write.evidence,
            )
            inserted += 1
        return inserted

    def list_table_links(
        self,
        table_id: uuid.UUID,
    ) -> list[tuple[AirflowDagTableRow, AirflowDagRow, AirflowInstanceRow]]:
        """Return links that point at one catalog table."""
        statement = (
            select(AirflowDagTableRow, AirflowDagRow, AirflowInstanceRow)
            .join(AirflowDagRow, AirflowDagRow.id == AirflowDagTableRow.dag_row_id)
            .join(AirflowInstanceRow, AirflowInstanceRow.id == AirflowDagRow.instance_id)
            .where(AirflowDagTableRow.catalog_table_id == table_id)
            .order_by(AirflowInstanceRow.name, AirflowDagRow.dag_id)
        )
        found: list[tuple[AirflowDagTableRow, AirflowDagRow, AirflowInstanceRow]] = []
        for row in self._session.execute(statement):
            link: object = row[0]
            dag: object = row[1]
            instance: object = row[2]
            if (
                isinstance(link, AirflowDagTableRow)
                and isinstance(dag, AirflowDagRow)
                and isinstance(instance, AirflowInstanceRow)
            ):
                found.append((link, dag, instance))
        return found

    def get_catalog_table(self, table_id: uuid.UUID) -> CatalogTableRow | None:
        """Return one catalog table."""
        return self._session.get(CatalogTableRow, table_id)

    def catalog_tables(self, table_ids: set[uuid.UUID]) -> dict[uuid.UUID, CatalogTableRow]:
        """Return catalog tables for the given ids."""
        if len(table_ids) == 0:
            return {}
        statement: Select[tuple[CatalogTableRow]] = select(CatalogTableRow).where(
            CatalogTableRow.id.in_(table_ids),
        )
        rows: list[CatalogTableRow] = list(self._session.scalars(statement))
        return {row.id: row for row in rows}

    def list_catalog_tables(self) -> list[CatalogTableRow]:
        """Return every catalog table for DAG name matching."""
        return list(self._session.scalars(select(CatalogTableRow)))

    def add_sync_run(
        self,
        *,
        instance_id: uuid.UUID,
        started_at: datetime,
        finished_at: datetime | None,
        status: str,
        counts: dict[str, object],
        sanitized_error: str | None,
    ) -> AirflowSyncRunRow:
        """Insert a sync run."""
        row: AirflowSyncRunRow = AirflowSyncRunRow(
            id=uuid.uuid4(),
            instance_id=instance_id,
            started_at=started_at,
            finished_at=finished_at,
            status=status,
            counts=counts,
            sanitized_error=sanitized_error,
        )
        return self._add(row)

    def list_sync_runs(self, instance_id: uuid.UUID) -> list[AirflowSyncRunRow]:
        """Return sync runs, newest first."""
        statement: Select[tuple[AirflowSyncRunRow]] = (
            select(AirflowSyncRunRow)
            .where(AirflowSyncRunRow.instance_id == instance_id)
            .order_by(AirflowSyncRunRow.started_at.desc())
        )
        return list(self._session.scalars(statement))

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

    def rollback(self) -> None:
        """Drop uncommitted work so a failed sync can record its error."""
        self._session.rollback()

    def add_attachment(
        self,
        *,
        instance_id: uuid.UUID | None,
        dag_row_id: uuid.UUID | None,
        space_id: uuid.UUID | None,
        scope_id: uuid.UUID | None,
    ) -> AirflowAttachmentRow:
        """Insert one instance or DAG attachment."""
        row: AirflowAttachmentRow = AirflowAttachmentRow(
            id=uuid.uuid4(),
            airflow_instance_id=instance_id,
            dag_row_id=dag_row_id,
            space_id=space_id,
            scope_id=scope_id,
            created_at=utc_now(),
        )
        return self._add(row)

    def get_attachment(self, attachment_id: uuid.UUID) -> AirflowAttachmentRow | None:
        """Return one attachment."""
        return self._session.get(AirflowAttachmentRow, attachment_id)

    def find_attachment(
        self,
        *,
        instance_id: uuid.UUID | None,
        dag_row_id: uuid.UUID | None,
        space_id: uuid.UUID | None,
        scope_id: uuid.UUID | None,
    ) -> AirflowAttachmentRow | None:
        """Return the attachment for this source and target, if one exists."""
        statement: Select[tuple[AirflowAttachmentRow]] = select(AirflowAttachmentRow).where(
            _id_match(AirflowAttachmentRow.airflow_instance_id, instance_id),
            _id_match(AirflowAttachmentRow.dag_row_id, dag_row_id),
            _id_match(AirflowAttachmentRow.space_id, space_id),
            _id_match(AirflowAttachmentRow.scope_id, scope_id),
        )
        return self._session.scalar(statement)

    def list_attachments(
        self,
        *,
        space_id: uuid.UUID | None,
        scope_id: uuid.UUID | None,
    ) -> list[AirflowAttachmentRow]:
        """Return attachments for one space or one scope."""
        statement: Select[tuple[AirflowAttachmentRow]] = select(AirflowAttachmentRow)
        if space_id is not None:
            statement = statement.where(AirflowAttachmentRow.space_id == space_id)
        if scope_id is not None:
            statement = statement.where(AirflowAttachmentRow.scope_id == scope_id)
        statement = statement.order_by(AirflowAttachmentRow.created_at)
        return list(self._session.scalars(statement))

    def list_attachments_touching(
        self,
        instance_id: uuid.UUID,
        dag_row_ids: set[uuid.UUID],
    ) -> list[AirflowAttachmentRow]:
        """Return attachments of this instance or of the given DAG rows."""
        clauses = [AirflowAttachmentRow.airflow_instance_id == instance_id]
        if len(dag_row_ids) > 0:
            clauses.append(AirflowAttachmentRow.dag_row_id.in_(dag_row_ids))
        statement: Select[tuple[AirflowAttachmentRow]] = select(AirflowAttachmentRow).where(
            or_(*clauses),
        )
        return list(self._session.scalars(statement))

    def list_attached_dags(
        self,
        scope_ids: set[uuid.UUID],
        space_ids: set[uuid.UUID],
    ) -> list[AttachedDag]:
        """Expand instance attachments to every synced DAG. Direct DAG rows win on overlap."""
        if len(scope_ids) == 0 and len(space_ids) == 0:
            return []
        clauses = []
        if len(scope_ids) > 0:
            clauses.append(AirflowAttachmentRow.scope_id.in_(scope_ids))
        if len(space_ids) > 0:
            clauses.append(AirflowAttachmentRow.space_id.in_(space_ids))
        statement: Select[tuple[AirflowAttachmentRow]] = select(AirflowAttachmentRow).where(
            or_(*clauses),
        )
        attachments: list[AirflowAttachmentRow] = list(self._session.scalars(statement))
        direct: dict[uuid.UUID, uuid.UUID] = {}
        by_instance: dict[uuid.UUID, uuid.UUID] = {}
        attachment: AirflowAttachmentRow
        for attachment in attachments:
            if attachment.dag_row_id is not None:
                direct[attachment.dag_row_id] = attachment.id
            elif attachment.airflow_instance_id is not None:
                by_instance[attachment.airflow_instance_id] = attachment.id
        self._merge_dag_memberships(scope_ids, space_ids, direct)
        self._merge_instance_memberships(scope_ids, space_ids, by_instance)
        dags: dict[uuid.UUID, AirflowDagRow] = self.dags_by_row_ids(set(direct))
        instance_id: uuid.UUID
        for instance_id in by_instance:
            dag: AirflowDagRow
            for dag in self.list_dags(instance_id):
                dags.setdefault(dag.id, dag)
        instances: dict[uuid.UUID, AirflowInstanceRow] = self.instances_by_ids(
            {dag.instance_id for dag in dags.values()},
        )
        found: list[AttachedDag] = []
        dag_row: AirflowDagRow
        for dag_row in dags.values():
            instance: AirflowInstanceRow | None = instances.get(dag_row.instance_id)
            if instance is None:
                continue
            if dag_row.id in direct:
                found.append(
                    AttachedDag(
                        dag=dag_row,
                        instance=instance,
                        via="dag",
                        attachment_id=direct[dag_row.id],
                    )
                )
                continue
            owner: uuid.UUID | None = by_instance.get(dag_row.instance_id)
            if owner is None:
                continue
            found.append(
                AttachedDag(
                    dag=dag_row,
                    instance=instance,
                    via="instance",
                    attachment_id=owner,
                )
            )
        found.sort(key=lambda item: (item.instance.name, item.dag.dag_id))
        return found

    def _merge_dag_memberships(
        self,
        scope_ids: set[uuid.UUID],
        space_ids: set[uuid.UUID],
        direct: dict[uuid.UUID, uuid.UUID],
    ) -> None:
        clauses: list[ColumnElement[bool]] = []
        if len(scope_ids) > 0:
            clauses.append(AssetMembershipRow.scope_id.in_(scope_ids))
        if len(space_ids) > 0:
            clauses.append(AssetMembershipRow.space_id.in_(space_ids))
        if len(clauses) == 0:
            return
        statement = (
            select(AssetMembershipRow, AirflowDagRow)
            .join(AirflowDagRow, AirflowDagRow.asset_id == AssetMembershipRow.asset_id)
            .where(or_(*clauses))
        )
        pair: tuple[AssetMembershipRow, AirflowDagRow]
        for pair in self._session.execute(statement):
            direct.setdefault(pair[1].id, pair[0].id)

    def _merge_instance_memberships(
        self,
        scope_ids: set[uuid.UUID],
        space_ids: set[uuid.UUID],
        by_instance: dict[uuid.UUID, uuid.UUID],
    ) -> None:
        clauses: list[ColumnElement[bool]] = []
        if len(scope_ids) > 0:
            clauses.append(AssetMembershipRow.scope_id.in_(scope_ids))
        if len(space_ids) > 0:
            clauses.append(AssetMembershipRow.space_id.in_(space_ids))
        if len(clauses) == 0:
            return
        statement = (
            select(AssetMembershipRow, AirflowInstanceRow)
            .join(AirflowInstanceRow, AirflowInstanceRow.asset_id == AssetMembershipRow.asset_id)
            .where(or_(*clauses))
        )
        pair: tuple[AssetMembershipRow, AirflowInstanceRow]
        for pair in self._session.execute(statement):
            by_instance.setdefault(pair[1].id, pair[0].id)

    def dag_by_asset(self, asset_id: uuid.UUID) -> AirflowDagRow | None:
        """Return the DAG snapshot linked to an asset."""
        statement: Select[tuple[AirflowDagRow]] = select(AirflowDagRow).where(
            AirflowDagRow.asset_id == asset_id,
        )
        return self._session.scalar(statement)

    def get_dag_row(self, dag_row_id: uuid.UUID) -> AirflowDagRow | None:
        """Return one DAG snapshot by its row id."""
        return self._session.get(AirflowDagRow, dag_row_id)

    def dags_by_row_ids(self, dag_row_ids: set[uuid.UUID]) -> dict[uuid.UUID, AirflowDagRow]:
        """Return DAG snapshots for the given row ids."""
        if len(dag_row_ids) == 0:
            return {}
        statement: Select[tuple[AirflowDagRow]] = select(AirflowDagRow).where(
            AirflowDagRow.id.in_(dag_row_ids),
        )
        rows: list[AirflowDagRow] = list(self._session.scalars(statement))
        return {row.id: row for row in rows}

    def instances_by_ids(
        self,
        instance_ids: set[uuid.UUID],
    ) -> dict[uuid.UUID, AirflowInstanceRow]:
        """Return instances for the given ids."""
        if len(instance_ids) == 0:
            return {}
        statement: Select[tuple[AirflowInstanceRow]] = select(AirflowInstanceRow).where(
            AirflowInstanceRow.id.in_(instance_ids),
        )
        rows: list[AirflowInstanceRow] = list(self._session.scalars(statement))
        return {row.id: row for row in rows}

    def list_mappings_for_dags(self, dag_row_ids: set[uuid.UUID]) -> list[AirflowDagTableRow]:
        """Return table links for the given DAG rows."""
        if len(dag_row_ids) == 0:
            return []
        statement: Select[tuple[AirflowDagTableRow]] = select(AirflowDagTableRow).where(
            AirflowDagTableRow.dag_row_id.in_(dag_row_ids),
        )
        return list(self._session.scalars(statement))

    def replace_llm_mappings(
        self,
        dag_row_ids: set[uuid.UUID],
        writes: list[LlmMappingWrite],
    ) -> int:
        """Replace LLM links for these DAGs. The caller commits. Other origins stay."""
        if len(dag_row_ids) == 0:
            return 0
        self._session.execute(
            delete(AirflowDagTableRow).where(
                AirflowDagTableRow.dag_row_id.in_(dag_row_ids),
                AirflowDagTableRow.origin == AirflowMappingOrigin.LLM.value,
            ),
        )
        self._flush()
        inserted: int = 0
        write: LlmMappingWrite
        for write in writes:
            if write.dag_row_id not in dag_row_ids:
                continue
            self.add_mapping(
                dag_row_id=write.dag_row_id,
                table_id=write.table_id,
                origin=AirflowMappingOrigin.LLM.value,
                direction=write.direction,
                evidence=write.evidence,
            )
            inserted += 1
        return inserted

    def _apply_dag(self, row: AirflowDagRow, write: DagWrite) -> None:
        row.display_name = write.display_name
        row.description = write.description
        row.is_paused = write.is_paused
        row.is_stale = write.is_stale
        row.has_import_errors = write.has_import_errors
        row.timetable_summary = write.timetable_summary
        row.timetable_description = write.timetable_description
        row.owners = write.owners
        row.tags = write.tags
        row.fileloc = write.fileloc
        row.bundle_name = write.bundle_name
        row.max_active_runs = write.max_active_runs
        row.next_dagrun_run_after = write.next_dagrun_run_after
        row.last_parsed_time = write.last_parsed_time
        row.tasks = write.tasks
        row.last_run = write.last_run
        row.run_stats = write.run_stats
        row.source_code = write.source_code
        row.source_version = write.source_version
        row.last_seen_at = utc_now()

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


def _id_match(
    column: InstrumentedAttribute[uuid.UUID | None],
    value: uuid.UUID | None,
) -> ColumnElement[bool]:
    if value is None:
        return column.is_(None)
    return column == value
