"""Turn an Airflow instance catalog into per-DAG findings and LLM table links."""

import logging
from typing import Protocol
from uuid import UUID

from intelegent_data_catalog.airflow.repository import LlmMappingWrite
from intelegent_data_catalog.core.logger import LOGGER_NAME
from intelegent_data_catalog.exceptions import ValidationAppError
from intelegent_data_catalog.knowledge.models import (
    AirflowAnalysisContext,
    AirflowCatalogContext,
    AirflowDagInput,
    AirflowProposal,
    DagFinding,
    DagTableLink,
    PreviousAirflowFindings,
)

logger: logging.Logger = logging.getLogger(f"{LOGGER_NAME}.knowledge")

_MAX_LINKS: int = 200


class AirflowRelationProposer(Protocol):
    """Selects per-DAG findings. Implementations may call a model."""

    def propose(self, context: AirflowCatalogContext) -> AirflowProposal:
        """Return findings whose DAGs and tables come from ``context``."""
        ...


class AirflowMappingWriter(Protocol):
    """Replaces LLM DAG links. Other origins stay. The caller commits."""

    def replace_llm_mappings(self, dag_row_ids: set[UUID], writes: list[LlmMappingWrite]) -> int:
        """Delete prior LLM rows for these DAGs and insert ``writes``."""
        ...


class AirflowGraphAppender(Protocol):
    """Accepts findings and stores the LLM table links."""

    def apply(
        self,
        context: AirflowCatalogContext,
        *,
        include_summary: bool = True,
    ) -> AirflowAnalysisContext:
        """Propose findings, store links, and return the instance context."""
        ...


class AirflowGraphBuilder(AirflowGraphAppender):
    """Accept model findings and replace LLM links for the analyzed DAGs."""

    def __init__(self, proposer: AirflowRelationProposer, mappings: AirflowMappingWriter) -> None:
        self._proposer: AirflowRelationProposer = proposer
        self._mappings: AirflowMappingWriter = mappings

    def apply(
        self,
        context: AirflowCatalogContext,
        *,
        include_summary: bool = True,
    ) -> AirflowAnalysisContext:
        """Propose findings, replace LLM links, and return the instance context."""
        proposed: AirflowProposal = self._proposer.propose(context)
        accepted: list[DagFinding] = accept_airflow_findings(context, proposed.dags)
        finding: DagFinding
        for finding in accepted:
            logger.info(
                "airflow finding instance_id=%s dag_id=%s links=%s",
                context.instance_id,
                finding.dag_id,
                len(finding.links),
            )
        dag_ids: set[UUID] = {dag.dag_row_id for dag in context.dags}
        writes: list[LlmMappingWrite] = [
            LlmMappingWrite(
                dag_row_id=link.dag_row_id,
                table_id=link.table_id,
                direction=link.direction.value,
                evidence=link.reason,
            )
            for item in accepted
            for link in item.links
        ]
        self._mappings.replace_llm_mappings(dag_ids, writes)
        summary: str | None = proposed.summary.strip()
        if summary == "" or not include_summary:
            summary = None
        return AirflowAnalysisContext(
            instance_id=context.instance_id,
            instance_name=context.instance_name,
            summary=summary,
            findings=accepted,
            dags_analyzed=len(context.dags),
            previous_links_kept=_previous_links_kept(context.previous, accepted),
        )


def accept_airflow_findings(
    context: AirflowCatalogContext,
    findings: list[DagFinding],
) -> list[DagFinding]:
    """Keep findings for DAGs in this call whose tables are candidates."""
    known: dict[UUID, AirflowDagInput] = {dag.dag_row_id: dag for dag in context.dags}
    candidates: set[UUID] = {table.id for table in context.candidate_tables}
    accepted: list[DagFinding] = []
    seen_dags: set[UUID] = set()
    total: int = 0
    finding: DagFinding
    for finding in findings:
        dag: AirflowDagInput | None = known.get(finding.dag_row_id)
        if dag is None:
            raise ValidationAppError("dag finding uses an unknown dag", field="dag")
        if finding.dag_id != dag.dag_id:
            raise ValidationAppError("dag finding dag_id does not match", field="dag")
        if finding.dag_row_id in seen_dags:
            raise ValidationAppError("duplicate dag finding", field="dag")
        seen_dags.add(finding.dag_row_id)
        links: list[DagTableLink] = []
        seen_links: set[tuple[UUID, UUID, str]] = set()
        link: DagTableLink
        for link in finding.links:
            if link.dag_row_id != finding.dag_row_id:
                raise ValidationAppError(
                    "table link dag does not match the finding",
                    field="table_link",
                )
            if link.table_id not in candidates:
                raise ValidationAppError("table link uses an unknown table", field="table_link")
            key: tuple[UUID, UUID, str] = (link.dag_row_id, link.table_id, link.direction.value)
            if key in seen_links:
                continue
            total += 1
            if total > _MAX_LINKS:
                raise ValidationAppError("too many table links", field="table_links")
            seen_links.add(key)
            links.append(link)
        accepted.append(
            DagFinding(
                dag_row_id=finding.dag_row_id,
                dag_id=finding.dag_id,
                summary=finding.summary.strip(),
                links=links,
            )
        )
    return accepted


def _previous_links_kept(
    previous: PreviousAirflowFindings | None,
    accepted: list[DagFinding],
) -> int:
    if previous is None:
        return 0
    keys: set[tuple[UUID, UUID, str]] = set()
    finding: DagFinding
    for finding in accepted:
        link: DagTableLink
        for link in finding.links:
            keys.add((link.dag_row_id, link.table_id, link.direction.value))
    kept: int = 0
    prior: DagFinding
    for prior in previous.findings:
        prior_link: DagTableLink
        for prior_link in prior.links:
            key: tuple[UUID, UUID, str] = (
                prior_link.dag_row_id,
                prior_link.table_id,
                prior_link.direction.value,
            )
            if key in keys:
                kept += 1
    return kept
