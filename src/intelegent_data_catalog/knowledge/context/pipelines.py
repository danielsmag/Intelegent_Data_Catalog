"""Attached Airflow DAGs for a scope or space. One model call per Airflow instance."""

import logging
from typing import Protocol
from uuid import UUID

from intelegent_data_catalog.airflow.repository import AirflowRepository, AttachedDag
from intelegent_data_catalog.core.logger import LOGGER_NAME
from intelegent_data_catalog.exceptions import CatalogError
from intelegent_data_catalog.knowledge.factories.airflow_agent_factory import AirflowAgentFactory
from intelegent_data_catalog.knowledge.models import (
    AirflowAnalysisContext,
    AirflowContextOptions,
    DagFinding,
    DagTableLink,
    PipelineInput,
)
from intelegent_data_catalog.knowledge.store import AirflowKnowledgeContextStore
from intelegent_data_catalog.persistence.enums import AirflowMappingDirection, AirflowMappingOrigin
from intelegent_data_catalog.persistence.models import AirflowDagTableRow

logger: logging.Logger = logging.getLogger(f"{LOGGER_NAME}.knowledge")

_MAX_PIPELINES: int = 30


class AttachedPipelineSource(Protocol):
    """DAGs attached to a scope or space, with the tables they read and write."""

    def for_scope(self, scope_id: UUID, table_ids: set[UUID]) -> list[PipelineInput]:
        """Return pipelines for one scope."""
        ...

    def for_space(
        self,
        space_id: UUID,
        scope_ids: set[UUID],
        table_ids: set[UUID],
    ) -> list[PipelineInput]:
        """Return pipelines for one space and the scopes inside it."""
        ...


class AttachedPipelineRunner:
    """Runs the Airflow agent once per instance, then returns pipeline facts."""

    def __init__(
        self,
        repository: AirflowRepository,
        agents: AirflowAgentFactory,
        snapshots: AirflowKnowledgeContextStore,
    ) -> None:
        self._repository: AirflowRepository = repository
        self._agents: AirflowAgentFactory = agents
        self._snapshots: AirflowKnowledgeContextStore = snapshots

    def for_scope(self, scope_id: UUID, table_ids: set[UUID]) -> list[PipelineInput]:
        """Analyze each attached instance once for this scope."""
        return self._collect({scope_id}, set(), table_ids)

    def for_space(
        self,
        space_id: UUID,
        scope_ids: set[UUID],
        table_ids: set[UUID],
    ) -> list[PipelineInput]:
        """Analyze each attached instance once for this space."""
        return self._collect(set(scope_ids), {space_id}, table_ids)

    def _collect(
        self,
        scope_ids: set[UUID],
        space_ids: set[UUID],
        table_ids: set[UUID],
    ) -> list[PipelineInput]:
        attached: list[AttachedDag] = self._repository.list_attached_dags(scope_ids, space_ids)
        grouped: dict[UUID, list[AttachedDag]] = {}
        item: AttachedDag
        for item in attached:
            grouped.setdefault(item.instance.id, []).append(item)
        findings: dict[UUID, DagFinding] = {}
        instance_id: UUID
        group: list[AttachedDag]
        for instance_id, group in grouped.items():
            analyzed: dict[UUID, DagFinding] | None = self._analyze(instance_id, group, table_ids)
            if analyzed is None:
                findings.update(_stored_findings(self._snapshots, instance_id))
                continue
            findings.update(analyzed)
        return _pipeline_inputs(attached, findings, self._repository, table_ids)

    def _analyze(
        self,
        instance_id: UUID,
        group: list[AttachedDag],
        table_ids: set[UUID],
    ) -> dict[UUID, DagFinding] | None:
        dag_row_ids: set[UUID] = {item.dag.id for item in group}
        try:
            built: AirflowAnalysisContext = self._agents.create(instance_id).build_context(
                AirflowContextOptions(),
                dag_row_ids=dag_row_ids,
                candidate_table_ids=table_ids,
            )
            self._snapshots.merge_findings(built)
        except CatalogError as err:
            logger.warning("airflow analysis failed instance_id=%s error=%s", instance_id, err)
            self._repository.rollback()
            return None
        except Exception:
            logger.warning("airflow analysis failed instance_id=%s", instance_id, exc_info=True)
            self._repository.rollback()
            return None
        found: dict[UUID, DagFinding] = {finding.dag_row_id: finding for finding in built.findings}
        return found


class AttachedPipelineReader:
    """Reads stored Airflow findings. Does not call a model."""

    def __init__(
        self,
        repository: AirflowRepository,
        snapshots: AirflowKnowledgeContextStore,
    ) -> None:
        self._repository: AirflowRepository = repository
        self._snapshots: AirflowKnowledgeContextStore = snapshots

    def for_scope(self, scope_id: UUID, table_ids: set[UUID]) -> list[PipelineInput]:
        """Return stored pipelines for one scope."""
        return self._collect({scope_id}, set(), table_ids)

    def for_space(
        self,
        space_id: UUID,
        scope_ids: set[UUID],
        table_ids: set[UUID],
    ) -> list[PipelineInput]:
        """Return stored pipelines for one space and its scopes."""
        return self._collect(set(scope_ids), {space_id}, table_ids)

    def _collect(
        self,
        scope_ids: set[UUID],
        space_ids: set[UUID],
        table_ids: set[UUID],
    ) -> list[PipelineInput]:
        attached: list[AttachedDag] = self._repository.list_attached_dags(scope_ids, space_ids)
        findings: dict[UUID, DagFinding] = {}
        seen: set[UUID] = set()
        item: AttachedDag
        for item in attached:
            if item.instance.id in seen:
                continue
            seen.add(item.instance.id)
            findings.update(_stored_findings(self._snapshots, item.instance.id))
        return _pipeline_inputs(attached, findings, self._repository, table_ids)


def _stored_findings(
    snapshots: AirflowKnowledgeContextStore,
    instance_id: UUID,
) -> dict[UUID, DagFinding]:
    saved: AirflowAnalysisContext | None = snapshots.get(instance_id)
    if saved is None:
        return {}
    return {finding.dag_row_id: finding for finding in saved.findings}


def _pipeline_inputs(
    attached: list[AttachedDag],
    findings: dict[UUID, DagFinding],
    repository: AirflowRepository,
    table_ids: set[UUID],
) -> list[PipelineInput]:
    mappings: dict[UUID, list[AirflowDagTableRow]] = {}
    link: AirflowDagTableRow
    for link in repository.list_mappings_for_dags({item.dag.id for item in attached}):
        mappings.setdefault(link.dag_row_id, []).append(link)
    built: list[PipelineInput] = []
    item: AttachedDag
    for item in attached:
        finding: DagFinding | None = findings.get(item.dag.id)
        summary: str | None = None
        if finding is not None and finding.summary.strip() != "":
            summary = finding.summary.strip()
        reads, writes, unknown = _directions(finding, mappings.get(item.dag.id, []), table_ids)
        built.append(
            PipelineInput(
                dag_row_id=item.dag.id,
                dag_id=item.dag.dag_id,
                instance_name=item.instance.name,
                description=_blank(item.dag.description),
                schedule=_blank(item.dag.timetable_summary),
                summary=summary,
                reads=reads,
                writes=writes,
                unknown=unknown,
            )
        )
    built.sort(key=lambda pipeline: (pipeline.instance_name, pipeline.dag_id))
    return built[:_MAX_PIPELINES]


def _directions(
    finding: DagFinding | None,
    mappings: list[AirflowDagTableRow],
    table_ids: set[UUID],
) -> tuple[list[UUID], list[UUID], list[UUID]]:
    reads: set[UUID] = set()
    writes: set[UUID] = set()
    unknown: set[UUID] = set()
    if finding is not None:
        link: DagTableLink
        for link in finding.links:
            _place(link.direction.value, link.table_id, table_ids, reads, writes, unknown)
    row: AirflowDagTableRow
    for row in mappings:
        if finding is not None and row.origin == AirflowMappingOrigin.LLM.value:
            continue
        _place(row.direction, row.catalog_table_id, table_ids, reads, writes, unknown)
    return _sorted(reads), _sorted(writes), _sorted(unknown)


def _place(
    direction: str,
    table_id: UUID,
    table_ids: set[UUID],
    reads: set[UUID],
    writes: set[UUID],
    unknown: set[UUID],
) -> None:
    if table_id not in table_ids:
        return
    if direction == AirflowMappingDirection.INPUT.value:
        reads.add(table_id)
        return
    if direction == AirflowMappingDirection.OUTPUT.value:
        writes.add(table_id)
        return
    unknown.add(table_id)


def _sorted(values: set[UUID]) -> list[UUID]:
    return sorted(values, key=str)


def _blank(value: str | None) -> str | None:
    if value is None:
        return None
    text: str = value.strip()
    if text == "":
        return None
    return text
