"""Load one Airflow instance for the Airflow agent. Reads do not change catalog rows."""

from typing import Protocol
from uuid import UUID

from intelegent_data_catalog.airflow.repository import AirflowRepository
from intelegent_data_catalog.core.settings import ContextSettings
from intelegent_data_catalog.exceptions import EntityNotFoundError
from intelegent_data_catalog.knowledge.models import (
    AirflowCatalogContext,
    AirflowContextOptions,
    AirflowDagInput,
    DagCandidateTable,
    DagTableRef,
    DagTaskSummary,
    PreviousAirflowFindings,
)
from intelegent_data_catalog.knowledge.store import SavedAirflowContext
from intelegent_data_catalog.persistence.enums import AirflowMappingDirection, AirflowMappingOrigin
from intelegent_data_catalog.persistence.models import (
    AirflowDagRow,
    AirflowDagTableRow,
    AirflowInstanceRow,
    CatalogTableRow,
)
from intelegent_data_catalog.registry.repository import RegistryRepository


class PreviousAirflowReader(Protocol):
    """Reads the last Airflow instance snapshot."""

    def saved(self, instance_id: UUID) -> SavedAirflowContext | None:
        """Return the snapshot, or none when the instance has never been built."""
        ...


class AirflowCatalogContextProvider(Protocol):
    """Supplies one Airflow instance. Implementations do not call a model."""

    def get_airflow_context(
        self,
        instance_id: UUID,
        dag_row_ids: set[UUID] | None,
        candidate_table_ids: set[UUID] | None,
        options: AirflowContextOptions | None = None,
    ) -> AirflowCatalogContext:
        """Return the DAGs in this call and the tables the model may link."""
        ...


class PostgresAirflowCatalogContextProvider:
    """Read one Airflow instance, its DAG snapshot, and catalog table candidates."""

    def __init__(
        self,
        airflow: AirflowRepository,
        registry: RegistryRepository,
        previous: PreviousAirflowReader,
        limits: ContextSettings | None = None,
    ) -> None:
        self._airflow: AirflowRepository = airflow
        self._registry: RegistryRepository = registry
        self._previous: PreviousAirflowReader = previous
        self._limits: ContextSettings = limits if limits is not None else ContextSettings()

    def get_airflow_context(
        self,
        instance_id: UUID,
        dag_row_ids: set[UUID] | None,
        candidate_table_ids: set[UUID] | None,
        options: AirflowContextOptions | None = None,
    ) -> AirflowCatalogContext:
        """Load the instance. ``dag_row_ids`` none means every synced DAG, capped."""
        selected: AirflowContextOptions = options or AirflowContextOptions()
        instance: AirflowInstanceRow | None = self._airflow.get_instance(instance_id)
        if instance is None:
            raise EntityNotFoundError("airflow instance", str(instance_id))
        rows: list[AirflowDagRow] = self._airflow.list_dags(instance_id)
        if dag_row_ids is not None:
            rows = [row for row in rows if row.id in dag_row_ids]
        rows.sort(key=lambda item: item.dag_id)
        truncated: bool = len(rows) > self._limits.airflow_max_dags
        chosen: list[AirflowDagRow] = rows[: self._limits.airflow_max_dags]
        include_source: bool = selected.include_source and not truncated
        mappings: list[AirflowDagTableRow] = self._airflow.list_mappings_for_dags(
            {row.id for row in chosen},
        )
        tables: dict[UUID, CatalogTableRow] = self._airflow.catalog_tables(
            {item.catalog_table_id for item in mappings},
        )
        known: dict[UUID, list[DagTableRef]] = _known_tables(mappings, tables)
        dags: list[AirflowDagInput] = [
            _dag_input(row, known.get(row.id, []), include_source, self._limits) for row in chosen
        ]
        candidates: list[DagCandidateTable] = self._candidates(
            instance_id,
            chosen,
            candidate_table_ids,
        )
        return AirflowCatalogContext(
            instance_id=instance.id,
            instance_name=instance.name,
            instance_description=_short_text(instance.description, self._limits.text_chars),
            instance_context=_short_text(instance.context, self._limits.text_chars),
            dags=dags,
            candidate_tables=candidates,
            previous=_previous(self._previous, instance_id, chosen, selected.include_previous),
        )

    def _candidates(
        self,
        instance_id: UUID,
        dags: list[AirflowDagRow],
        explicit: set[UUID] | None,
    ) -> list[DagCandidateTable]:
        if explicit is not None:
            loaded: dict[UUID, CatalogTableRow] = self._airflow.catalog_tables(explicit)
            ordered: list[CatalogTableRow] = [
                loaded[table_id] for table_id in explicit if table_id in loaded
            ]
            ordered.sort(
                key=lambda item: (item.physical_catalog, item.physical_namespace, item.name),
            )
            capped: list[CatalogTableRow] = ordered[: self._limits.airflow_max_candidate_tables]
            return [_candidate(row, self._limits.text_chars) for row in capped]
        del instance_id, dags
        combined: list[CatalogTableRow] = self._airflow.list_catalog_tables()
        return [
            _candidate(row, self._limits.text_chars)
            for row in combined[: self._limits.airflow_max_candidate_tables]
        ]


def _dag_input(
    row: AirflowDagRow,
    known: list[DagTableRef],
    include_source: bool,
    limits: ContextSettings,
) -> AirflowDagInput:
    schedule: str | None = _short_text(
        row.timetable_summary or row.timetable_description,
        limits.text_chars,
    )
    return AirflowDagInput(
        dag_row_id=row.id,
        dag_id=row.dag_id,
        display_name=row.display_name,
        description=_short_text(row.description, limits.text_chars),
        schedule=schedule,
        owners=_strings(row.owners),
        tags=_strings(row.tags),
        tasks=_tasks(row.tasks, limits.airflow_max_tasks),
        source_code=_source(row.source_code, include_source, limits.airflow_source_chars),
        known_tables=known,
    )


def _known_tables(
    mappings: list[AirflowDagTableRow],
    tables: dict[UUID, CatalogTableRow],
) -> dict[UUID, list[DagTableRef]]:
    grouped: dict[UUID, list[DagTableRef]] = {}
    item: AirflowDagTableRow
    for item in mappings:
        table: CatalogTableRow | None = tables.get(item.catalog_table_id)
        if table is None:
            continue
        try:
            direction: AirflowMappingDirection = AirflowMappingDirection(item.direction)
            origin: AirflowMappingOrigin = AirflowMappingOrigin(item.origin)
        except ValueError:
            continue
        grouped.setdefault(item.dag_row_id, []).append(
            DagTableRef(
                table_id=table.id,
                qualified_name=_qualified(table),
                direction=direction,
                origin=origin,
            )
        )
    return grouped


def _candidate(row: CatalogTableRow, text_chars: int) -> DagCandidateTable:
    return DagCandidateTable(
        id=row.id,
        qualified_name=_qualified(row),
        description=_short_text(row.description or row.source_description, text_chars),
    )


def _previous(
    previous: PreviousAirflowReader,
    instance_id: UUID,
    dags: list[AirflowDagRow],
    include_previous: bool,
) -> PreviousAirflowFindings | None:
    if not include_previous:
        return None
    saved: SavedAirflowContext | None = previous.saved(instance_id)
    if saved is None:
        return None
    wanted: set[UUID] = {row.id for row in dags}
    return PreviousAirflowFindings(
        summary=saved.context.summary,
        findings=[item for item in saved.context.findings if item.dag_row_id in wanted],
        built_at=saved.built_at,
    )


def _qualified(table: CatalogTableRow) -> str:
    return f"{table.physical_catalog}.{table.physical_namespace}.{table.physical_table}"


def _tasks(value: object, max_tasks: int) -> list[DagTaskSummary]:
    if not isinstance(value, list):
        return []
    found: list[DagTaskSummary] = []
    item: object
    for item in value:
        if not isinstance(item, dict):
            continue
        task_id: object = item.get("task_id")
        if not isinstance(task_id, str) or task_id.strip() == "":
            continue
        operator: object = item.get("operator_name")
        operator_name: str | None = operator if isinstance(operator, str) else None
        downstream: list[str] = []
        raw_down: object = item.get("downstream_task_ids")
        if isinstance(raw_down, list):
            part: object
            for part in raw_down:
                if isinstance(part, str):
                    downstream.append(part)
        found.append(
            DagTaskSummary(
                task_id=task_id,
                operator_name=operator_name,
                downstream_task_ids=downstream,
            )
        )
        if len(found) >= max_tasks:
            break
    return found


def _strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    found: list[str] = []
    item: object
    for item in value:
        if isinstance(item, str):
            found.append(item)
    return found


def _source(value: str | None, include: bool, source_chars: int) -> str | None:
    if not include:
        return None
    return _clip(value, source_chars)


def _short_text(value: str | None, text_chars: int) -> str | None:
    return _clip(value, text_chars)


def _clip(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    text: str = value.strip()
    if text == "":
        return None
    if len(text) <= limit:
        return text
    return text[:limit]
