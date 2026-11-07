"""Airflow instance registry, live metadata, and DAG snapshot sync."""

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from uuid import UUID

import httpx
from sqlalchemy.orm import Session

from intelegent_data_catalog.agents.sync import AgentSync
from intelegent_data_catalog.airflow.mapping import (
    AssetView,
    DagView,
    MappingCandidate,
    TableView,
    match_dag_tables,
)
from intelegent_data_catalog.airflow.reads import (
    dag_detail,
    dag_read,
    instance_read,
    mapping_read,
    sync_run_read,
    table_dag_read,
)
from intelegent_data_catalog.airflow.repository import (
    AirflowRepository,
    DagWrite,
    MappingWrite,
)
from intelegent_data_catalog.airflow.schemas import (
    AirflowConnectionTest,
    AirflowConnectionTestRequest,
    AirflowDagDetail,
    AirflowDagRead,
    AirflowDagRunRead,
    AirflowDagSourceRead,
    AirflowDagTableCreate,
    AirflowDagTableRead,
    AirflowHealthView,
    AirflowImportErrorView,
    AirflowInstanceCreate,
    AirflowInstanceOverview,
    AirflowInstanceRead,
    AirflowInstanceUpdate,
    AirflowPluginView,
    AirflowPoolView,
    AirflowProviderView,
    AirflowSyncRunRead,
    AirflowTableDagRead,
)
from intelegent_data_catalog.core.settings import AirflowSettings
from intelegent_data_catalog.exceptions import (
    AirflowUnavailableError,
    ConflictError,
    EntityNotFoundError,
    ValidationAppError,
)
from intelegent_data_catalog.knowledge.models import AirflowAnalysisContext, DagFinding
from intelegent_data_catalog.knowledge.store import AirflowKnowledgeContextStore
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.enums import (
    AirflowInstanceStatus,
    AirflowMappingOrigin,
    ContextOrigin,
    SyncRunStatus,
)
from intelegent_data_catalog.persistence.models import (
    AirflowDagRow,
    AirflowDagTableRow,
    AirflowInstanceRow,
    AirflowSyncRunRow,
    CatalogTableRow,
)
from intelegent_data_catalog.registry.identifiers import slugify
from intelegent_data_catalog.sources.airflow.client import AirflowClient
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
    AirflowRunStat,
    AirflowTask,
    AssetDagRef,
    AssetTaskRef,
)

_NOTE_LIMIT: int = 300
_ERROR_LIMIT: int = 4000
_STACK_LIMIT: int = 2000


class AirflowService:
    """Registers Airflow 3 instances and syncs their DAGs into the catalog."""

    def __init__(
        self,
        session: Session,
        http_client: httpx.Client,
        settings: AirflowSettings,
        logger: logging.Logger,
    ) -> None:
        self._repo: AirflowRepository = AirflowRepository(session)
        self._knowledge: AirflowKnowledgeContextStore = AirflowKnowledgeContextStore(session)
        self._http: httpx.Client = http_client
        self._settings: AirflowSettings = settings
        self._logger: logging.Logger = logger

    def create_instance(self, body: AirflowInstanceCreate) -> AirflowInstanceRead:
        """Store an instance. The password is not part of the response."""
        slug: str = _slug_for(body.name)
        self._ensure_slug(slug, None)
        self._ensure_base_url(body.base_url, None)
        row: AirflowInstanceRow = self._repo.add_instance(
            name=body.name,
            slug=slug,
            base_url=body.base_url,
            username=body.username,
            password=body.password,
            verify_tls=body.verify_tls,
            description=body.description,
            status=AirflowInstanceStatus.ACTIVE.value,
        )
        AgentSync(self._repo.session).ensure_instance(row)
        self._repo.commit()
        return instance_read(row, 0)

    def list_instances(self) -> list[AirflowInstanceRead]:
        """List instances with synced DAG counts."""
        counts: dict[UUID, int] = self._repo.dag_counts()
        return [
            instance_read(row, counts.get(row.id, 0)) for row in self._repo.list_instances()
        ]

    def get_instance(self, instance_id: UUID) -> AirflowInstanceRead:
        """Return one instance."""
        row: AirflowInstanceRow = self._instance(instance_id)
        return instance_read(row, self._repo.dag_counts().get(row.id, 0))

    def update_instance(
        self,
        instance_id: UUID,
        patch: AirflowInstanceUpdate,
    ) -> AirflowInstanceRead:
        """Patch an instance. An omitted password is left unchanged."""
        row: AirflowInstanceRow = self._instance(instance_id)
        changes: dict[str, object] = patch.model_dump(exclude_unset=True)
        if "name" in changes:
            name: str = _required(patch.name, "name")
            slug: str = _slug_for(name)
            self._ensure_slug(slug, row.id)
            row.name = name
            row.slug = slug
        if "base_url" in changes:
            base_url: str = _required(patch.base_url, "base_url")
            self._ensure_base_url(base_url, row.id)
            row.base_url = base_url
        if "username" in changes:
            row.username = _required(patch.username, "username")
        if "password" in changes:
            row.password = _required(patch.password, "password")
        if "verify_tls" in changes:
            row.verify_tls = _required(patch.verify_tls, "verify_tls")
        if "description" in changes:
            row.description = patch.description
        if "context" in changes:
            _apply_user_context(row, patch.context)
        if "status" in changes:
            status: AirflowInstanceStatus = _required(patch.status, "status")
            row.status = status.value
        row.updated_at = utc_now()
        AgentSync(self._repo.session).ensure_instance(row)
        self._repo.commit()
        return instance_read(row, self._repo.dag_counts().get(row.id, 0))

    def delete_instance(self, instance_id: UUID) -> None:
        """Delete an instance, its snapshots, and the linked agent."""
        row: AirflowInstanceRow = self._instance(instance_id)
        AgentSync(self._repo.session).delete_instance(row)
        self._repo.delete(row)
        self._repo.commit()

    def test_connection(self, body: AirflowConnectionTestRequest) -> AirflowConnectionTest:
        """Check version, health, and credentials without saving them."""
        version: str | None = None
        health: AirflowHealthView | None = None
        try:
            with self._open(body.base_url, body.username, body.password, body.verify_tls) as client:
                version = client.version().version
                health = _health_view(client.health())
                client.authenticate()
        except AirflowUnavailableError as err:
            return AirflowConnectionTest(ok=False, version=version, health=health, error=str(err))
        return AirflowConnectionTest(ok=True, version=version, health=health, error=None)

    def sync(self, instance_id: UUID) -> AirflowSyncRunRead:
        """Replace the DAG snapshot and discovered table links."""
        row: AirflowInstanceRow = self._instance(instance_id)
        self._ensure_active(row)
        started: datetime = utc_now()
        try:
            return self._sync_instance(row, started)
        except AirflowUnavailableError as err:
            self._repo.rollback()
            self._record_failure(instance_id, started, str(err))
            raise

    def list_sync_runs(self, instance_id: UUID) -> list[AirflowSyncRunRead]:
        """List recorded syncs for one instance."""
        self._instance(instance_id)
        return [sync_run_read(row) for row in self._repo.list_sync_runs(instance_id)]

    def overview(self, instance_id: UUID) -> AirflowInstanceOverview:
        """Read live Airflow metadata. Each section fails on its own."""
        row: AirflowInstanceRow = self._instance(instance_id)
        self._ensure_active(row)
        with self._open(row.base_url, row.username, row.password, row.verify_tls) as client:
            version, version_error = _capture(lambda: client.version().version)
            health, health_error = _capture(lambda: _health_view(client.health()))
            pools, pools_error = _capture(lambda: [_pool_view(item) for item in client.pools()])
            providers, providers_error = _capture(
                lambda: [_provider_view(item) for item in client.providers()],
            )
            plugins, plugins_error = _capture(
                lambda: [_plugin_view(item) for item in client.plugins()],
            )
            import_errors, import_errors_error = _capture(
                lambda: [_import_error_view(item) for item in client.import_errors()],
            )
        return AirflowInstanceOverview(
            version=version,
            version_error=version_error,
            health=health,
            health_error=health_error,
            pools=pools,
            pools_error=pools_error,
            providers=providers,
            providers_error=providers_error,
            plugins=plugins,
            plugins_error=plugins_error,
            import_errors=import_errors,
            import_errors_error=import_errors_error,
        )

    def list_dags(self, instance_id: UUID) -> list[AirflowDagRead]:
        """Return the synced DAG snapshot."""
        self._instance(instance_id)
        return self._dag_reads(instance_id)

    def get_dag(self, instance_id: UUID, dag_id: str) -> AirflowDagDetail:
        """Return one synced DAG, including tasks and source."""
        dag: AirflowDagRow = self._dag(instance_id, dag_id)
        tables: list[AirflowDagTableRead] = self._tables_for({dag.id: dag}).get(dag.id, [])
        return dag_detail(dag, tables)

    def list_dag_runs(
        self,
        instance_id: UUID,
        dag_id: str,
        limit: int,
    ) -> list[AirflowDagRunRead]:
        """Read recent runs live from Airflow."""
        row: AirflowInstanceRow = self._instance(instance_id)
        self._ensure_active(row)
        self._dag(instance_id, dag_id)
        with self._open(row.base_url, row.username, row.password, row.verify_tls) as client:
            runs: list[AirflowDagRun] = client.dag_runs(dag_id, limit)
        return [_run_read(run) for run in runs]

    def dag_source(self, instance_id: UUID, dag_id: str) -> AirflowDagSourceRead:
        """Return the source stored by the last sync."""
        dag: AirflowDagRow = self._dag(instance_id, dag_id)
        return AirflowDagSourceRead(
            dag_id=dag.dag_id,
            content=dag.source_code,
            version_number=dag.source_version,
        )

    def add_table_mapping(
        self,
        instance_id: UUID,
        dag_id: str,
        body: AirflowDagTableCreate,
    ) -> AirflowDagTableRead:
        """Store a manual DAG-to-table link."""
        dag: AirflowDagRow = self._dag(instance_id, dag_id)
        table: CatalogTableRow | None = self._repo.get_catalog_table(body.catalog_table_id)
        if table is None:
            raise EntityNotFoundError("catalog table", str(body.catalog_table_id))
        existing: AirflowDagTableRow | None = self._repo.find_mapping(
            dag.id,
            table.id,
            AirflowMappingOrigin.MANUAL.value,
        )
        if existing is not None:
            raise ConflictError("manual mapping already exists")
        row: AirflowDagTableRow = self._repo.add_mapping(
            dag_row_id=dag.id,
            table_id=table.id,
            origin=AirflowMappingOrigin.MANUAL.value,
            direction=body.direction.value,
            evidence=None,
        )
        self._repo.commit()
        return mapping_read(row, dag, table)

    def remove_table_mapping(self, instance_id: UUID, dag_id: str, table_id: UUID) -> None:
        """Delete the manual link. Discovered links stay until the next sync."""
        dag: AirflowDagRow = self._dag(instance_id, dag_id)
        links: list[AirflowDagTableRow] = self._repo.list_links(dag.id, table_id)
        manual: AirflowDagTableRow | None = None
        link: AirflowDagTableRow
        for link in links:
            if link.origin == AirflowMappingOrigin.MANUAL.value:
                manual = link
                break
        if manual is not None:
            self._repo.delete(manual)
            self._repo.commit()
            return
        if len(links) > 0:
            raise ConflictError("only manual mappings can be removed")
        raise EntityNotFoundError("airflow dag table", str(table_id))





    def dags_for_table(self, table_id: UUID) -> list[AirflowTableDagRead]:
        """Return DAG links for one catalog table."""
        if self._repo.get_catalog_table(table_id) is None:
            raise EntityNotFoundError("catalog table", str(table_id))
        return [
            table_dag_read(link, dag, instance)
            for link, dag, instance in self._repo.list_table_links(table_id)
        ]






    def _finding_summaries(self, instance_ids: set[UUID]) -> dict[UUID, str]:
        found: dict[UUID, str] = {}
        instance_id: UUID
        for instance_id in instance_ids:
            saved: AirflowAnalysisContext | None = self._knowledge.get(instance_id)
            if saved is None:
                continue
            finding: DagFinding
            for finding in saved.findings:
                text: str = finding.summary.strip()
                if text != "":
                    found[finding.dag_row_id] = text
        return found

    def _sync_instance(self, row: AirflowInstanceRow, started: datetime) -> AirflowSyncRunRead:
        notes: list[str] = []
        dag_errors: int = 0
        writes: list[DagWrite] = []
        assets: list[AirflowAsset] = []
        with self._open(row.base_url, row.username, row.password, row.verify_tls) as client:
            dags: list[AirflowDag] = client.list_dags()
            loaded_assets, assets_error = _capture(client.assets)
            if assets_error is not None:
                notes.append(_clip(f"assets: {assets_error}"))
            elif loaded_assets is not None:
                assets = loaded_assets
            stats, stats_error = _capture(lambda: client.dag_stats([dag.dag_id for dag in dags]))
            if stats_error is not None:
                notes.append(_clip(f"dagStats: {stats_error}"))
            stats_by_id: dict[str, list[AirflowRunStat]] = {}
            if stats is not None:
                item: AirflowDagStats
                for item in stats:
                    stats_by_id[item.dag_id] = item.stats
            dag: AirflowDag
            for dag in dags:
                write, error = self._snapshot_dag(client, dag, stats_by_id.get(dag.dag_id, []))
                writes.append(write)
                if error is not None:
                    dag_errors += 1
                    notes.append(_clip(f"{dag.dag_id}: {error}"))
        for write in writes:
            self._repo.upsert_dag(row.id, write)
        removed: int = self._repo.delete_missing_dags(row.id, {item.dag_id for item in writes})
        candidates: list[MappingCandidate] = match_dag_tables(
            [DagView(item.dag_id, item.description, item.source_code) for item in writes],
            [_asset_view(asset) for asset in assets],
            [_table_view(table) for table in self._repo.list_catalog_tables()],
        )
        mapping_count: int = self._repo.replace_discovered_mappings(
            row.id,
            [_mapping_write(item) for item in candidates],
        )
        finished: datetime = utc_now()
        error_text: str | None = _join_notes(notes)
        status: SyncRunStatus = SyncRunStatus.PARTIAL if notes else SyncRunStatus.SUCCEEDED
        counts: dict[str, object] = {
            "dags_seen": len(writes),
            "dags_removed": removed,
            "mappings": mapping_count,
            "dag_errors": dag_errors,
        }
        run: AirflowSyncRunRow = self._repo.add_sync_run(
            instance_id=row.id,
            started_at=started,
            finished_at=finished,
            status=status.value,
            counts=counts,
            sanitized_error=error_text,
        )
        row.last_synced_at = finished
        row.last_error = error_text
        row.updated_at = finished
        self._repo.commit()
        if error_text is not None:
            self._logger.warning("airflow sync partial for %s: %s", row.base_url, error_text)
        return sync_run_read(run)

    def _snapshot_dag(
        self,
        client: AirflowClient,
        dag: AirflowDag,
        stats: list[AirflowRunStat],
    ) -> tuple[DagWrite, str | None]:
        errors: list[str] = []
        tasks, task_error = _capture(lambda: client.dag_tasks(dag.dag_id))
        if task_error is not None:
            errors.append(task_error)
        runs, run_error = _capture(lambda: client.dag_runs(dag.dag_id, 1))
        if run_error is not None:
            errors.append(run_error)
        source, source_error = _capture(lambda: client.dag_source(dag.dag_id))
        if source_error is not None:
            errors.append(source_error)
        task_rows: list[AirflowTask] = [] if tasks is None else tasks
        last_run: AirflowDagRun | None = None
        if runs is not None and len(runs) > 0:
            last_run = runs[0]
        source_row: AirflowDagSource | None = source
        message: str | None = "; ".join(errors) if len(errors) > 0 else None
        return (
            DagWrite(
                dag_id=dag.dag_id,
                display_name=dag.dag_display_name,
                description=dag.description,
                is_paused=dag.is_paused,
                is_stale=dag.is_stale,
                has_import_errors=dag.has_import_errors,
                timetable_summary=dag.timetable_summary,
                timetable_description=dag.timetable_description,
                owners=list(dag.owners),
                tags=[tag.name for tag in dag.tags],
                fileloc=dag.fileloc,
                bundle_name=dag.bundle_name,
                max_active_runs=dag.max_active_runs,
                next_dagrun_run_after=dag.next_dagrun_run_after,
                last_parsed_time=dag.last_parsed_time,
                tasks=[_dump(task) for task in task_rows],
                last_run=None if last_run is None else _dump(last_run),
                run_stats=[_dump(stat) for stat in stats],
                source_code=None if source_row is None else source_row.content,
                source_version=None if source_row is None else source_row.version_number,
            ),
            message,
        )

    def _record_failure(self, instance_id: UUID, started: datetime, message: str) -> None:
        instance: AirflowInstanceRow | None = self._repo.get_instance(instance_id)
        if instance is None:
            return
        finished: datetime = utc_now()
        clipped: str = message[:_ERROR_LIMIT]
        instance.last_error = clipped
        instance.updated_at = finished
        self._repo.add_sync_run(
            instance_id=instance_id,
            started_at=started,
            finished_at=finished,
            status=SyncRunStatus.FAILED.value,
            counts={"dags_seen": 0, "dags_removed": 0, "mappings": 0, "dag_errors": 0},
            sanitized_error=clipped,
        )
        self._repo.commit()

    def _dag_reads(self, instance_id: UUID) -> list[AirflowDagRead]:
        dags: list[AirflowDagRow] = self._repo.list_dags(instance_id)
        grouped: dict[UUID, list[AirflowDagTableRead]] = self._tables_for(
            {dag.id: dag for dag in dags},
        )
        return [dag_read(dag, grouped.get(dag.id, [])) for dag in dags]

    def _tables_for(
        self,
        dags: dict[UUID, AirflowDagRow],
    ) -> dict[UUID, list[AirflowDagTableRead]]:
        if len(dags) == 0:
            return {}
        instance_id: UUID = next(iter(dags.values())).instance_id
        mappings: list[AirflowDagTableRow] = self._repo.list_mappings_for_instance(instance_id)
        tables: dict[UUID, CatalogTableRow] = self._repo.catalog_tables(
            {item.catalog_table_id for item in mappings},
        )
        grouped: dict[UUID, list[AirflowDagTableRead]] = {}
        item: AirflowDagTableRow
        for item in mappings:
            dag: AirflowDagRow | None = dags.get(item.dag_row_id)
            table: CatalogTableRow | None = tables.get(item.catalog_table_id)
            if dag is None or table is None:
                continue
            grouped.setdefault(item.dag_row_id, []).append(mapping_read(item, dag, table))
        return grouped

    def _instance(self, instance_id: UUID) -> AirflowInstanceRow:
        row: AirflowInstanceRow | None = self._repo.get_instance(instance_id)
        if row is None:
            raise EntityNotFoundError("airflow instance", str(instance_id))
        return row

    def _dag(self, instance_id: UUID, dag_id: str) -> AirflowDagRow:
        self._instance(instance_id)
        row: AirflowDagRow | None = self._repo.find_dag(instance_id, dag_id)
        if row is None:
            raise EntityNotFoundError("airflow dag", dag_id)
        return row

    def _ensure_active(self, row: AirflowInstanceRow) -> None:
        if row.status != AirflowInstanceStatus.ACTIVE.value:
            raise ValidationAppError("airflow instance is disabled", field="status")

    def _ensure_slug(self, slug: str, current_id: UUID | None) -> None:
        existing: AirflowInstanceRow | None = self._repo.find_instance_by_slug(slug)
        if existing is not None and existing.id != current_id:
            raise ConflictError("airflow instance slug already exists")

    def _ensure_base_url(self, base_url: str, current_id: UUID | None) -> None:
        existing: AirflowInstanceRow | None = self._repo.find_instance_by_base_url(base_url)
        if existing is not None and existing.id != current_id:
            raise ConflictError("airflow instance base_url already exists")

    @contextmanager
    def _open(
        self,
        base_url: str,
        username: str,
        password: str,
        verify_tls: bool,
    ) -> Iterator[AirflowClient]:
        owned: httpx.Client | None = None
        http_client: httpx.Client = self._http
        if not verify_tls:
            owned = httpx.Client(timeout=self._settings.timeout_seconds, verify=False)
            http_client = owned
        client: AirflowClient = AirflowClient(
            base_url=base_url,
            username=username,
            password=password,
            timeout_seconds=self._settings.timeout_seconds,
            http_client=http_client,
            logger=self._logger,
        )
        try:
            yield client
        finally:
            if owned is not None:
                owned.close()






def _apply_user_context(row: AirflowInstanceRow, value: str | None) -> None:
    text: str | None = None if value is None else value.strip()
    if text == "":
        text = None
    row.context = text
    row.context_origin = None if text is None else ContextOrigin.USER.value


def _slug_for(name: str) -> str:
    try:
        return slugify(name)
    except ValueError as err:
        raise ValidationAppError(str(err), field="name") from err


def _required[T](value: T | None, field: str) -> T:
    if value is None:
        raise ValidationAppError(f"{field} cannot be null", field=field)
    return value


def _capture[T](load: Callable[[], T]) -> tuple[T | None, str | None]:
    try:
        loaded: T = load()
    except AirflowUnavailableError as err:
        return None, str(err)
    return loaded, None


def _health_view(health: AirflowHealth) -> AirflowHealthView:
    triggerer: str | None = None
    if health.triggerer is not None:
        triggerer = health.triggerer.status
    processor: str | None = None
    if health.dag_processor is not None:
        processor = health.dag_processor.status
    return AirflowHealthView(
        metadatabase=health.metadatabase.status,
        scheduler=health.scheduler.status,
        triggerer=triggerer,
        dag_processor=processor,
    )


def _pool_view(pool: AirflowPool) -> AirflowPoolView:
    return AirflowPoolView(
        name=pool.name,
        slots=pool.slots,
        open_slots=pool.open_slots,
        description=pool.description,
    )


def _provider_view(provider: AirflowProvider) -> AirflowProviderView:
    return AirflowProviderView(package_name=provider.package_name, version=provider.version)


def _plugin_view(plugin: AirflowPlugin) -> AirflowPluginView:
    return AirflowPluginView(name=plugin.name, source=plugin.source)


def _import_error_view(error: AirflowImportError) -> AirflowImportErrorView:
    return AirflowImportErrorView(
        filename=error.filename,
        timestamp=error.timestamp,
        bundle_name=error.bundle_name,
        stack_trace=_clip_text(error.stack_trace, _STACK_LIMIT),
    )


def _run_read(run: AirflowDagRun) -> AirflowDagRunRead:
    return AirflowDagRunRead(
        dag_run_id=run.dag_run_id,
        state=run.state,
        run_type=run.run_type,
        start_date=run.start_date,
        end_date=run.end_date,
        logical_date=run.logical_date,
        duration=run.duration,
    )


def _asset_view(asset: AirflowAsset) -> AssetView:
    consuming: set[str] = _dag_ids(asset.consuming_tasks)
    consuming.update(_dag_ids(asset.consuming_dags))
    return AssetView(
        uri=asset.uri,
        producing_dag_ids=frozenset(_dag_ids(asset.producing_tasks)),
        consuming_dag_ids=frozenset(consuming),
        scheduled_dag_ids=frozenset(_dag_ids(asset.scheduled_dags)),
    )


def _dag_ids(refs: list[AssetTaskRef] | list[AssetDagRef]) -> set[str]:
    found: set[str] = set()
    ref: AssetTaskRef | AssetDagRef
    for ref in refs:
        if ref.dag_id is not None and ref.dag_id != "":
            found.add(ref.dag_id)
    return found


def _table_view(table: CatalogTableRow) -> TableView:
    return TableView(
        table_id=table.id,
        catalog=table.physical_catalog,
        namespace=table.physical_namespace,
        table=table.physical_table,
    )


def _mapping_write(candidate: MappingCandidate) -> MappingWrite:
    return MappingWrite(
        dag_id=candidate.dag_id,
        table_id=candidate.table_id,
        origin=candidate.origin.value,
        direction=candidate.direction.value,
        evidence=candidate.evidence,
    )


def _dump(model: AirflowTask | AirflowDagRun | AirflowRunStat) -> dict[str, object]:
    raw: object = model.model_dump(mode="json")
    if not isinstance(raw, dict):
        return {}
    dumped: dict[str, object] = {}
    key: object
    value: object
    for key, value in raw.items():
        if isinstance(key, str):
            dumped[key] = value
    return dumped


def _clip(note: str) -> str:
    if len(note) <= _NOTE_LIMIT:
        return note
    return note[:_NOTE_LIMIT]


def _clip_text(value: str | None, limit: int) -> str | None:
    if value is None or len(value) <= limit:
        return value
    return value[:limit]


def _join_notes(notes: list[str]) -> str | None:
    if len(notes) == 0:
        return None
    joined: str = "; ".join(notes)
    if len(joined) <= _ERROR_LIMIT:
        return joined
    return joined[:_ERROR_LIMIT]
