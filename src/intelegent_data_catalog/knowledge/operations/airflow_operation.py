"""Logical manager for one Airflow instance. Holds no chat history."""

from typing import Protocol
from uuid import UUID

from intelegent_data_catalog.exceptions import LlmUnavailableError, ValidationAppError
from intelegent_data_catalog.knowledge.context.airflow_catalog import AirflowCatalogContextProvider
from intelegent_data_catalog.knowledge.context.airflow_graph import AirflowGraphAppender
from intelegent_data_catalog.knowledge.models import (
    AirflowAnalysisContext,
    AirflowCatalogContext,
    AirflowContextOptions,
    AirflowContextProposal,
)
from intelegent_data_catalog.llm.agent import ProposalAgent
from intelegent_data_catalog.persistence.enums import ContextOrigin


class AirflowContextWriter(Protocol):
    """Stores business context on an Airflow instance row."""

    def save_airflow_instance(self, instance_id: UUID, text: str, origin: ContextOrigin) -> None:
        """Replace the instance context."""
        ...


class AirflowInstanceAgent:
    """Load one Airflow instance and ask for per-DAG findings in one model call.

    One agent covers every DAG of that instance included in the call.
    DAGs from another instance need another agent.
    ``describe`` asks for business context and stores it as ``LLM``.
    """

    def __init__(
        self,
        instance_id: UUID,
        catalog: AirflowCatalogContextProvider,
        graph_builder: AirflowGraphAppender | None = None,
        describer: ProposalAgent[AirflowCatalogContext, AirflowContextProposal] | None = None,
        context_writer: AirflowContextWriter | None = None,
    ) -> None:
        self._instance_id: UUID = instance_id
        self._catalog: AirflowCatalogContextProvider = catalog
        self._graph_builder: AirflowGraphAppender | None = graph_builder
        self._describer: ProposalAgent[AirflowCatalogContext, AirflowContextProposal] | None = (
            describer
        )
        self._context_writer: AirflowContextWriter | None = context_writer

    @property
    def instance_id(self) -> UUID:
        """Airflow instance this agent was created for."""
        return self._instance_id

    def get_metadata(self) -> AirflowCatalogContext:
        """Load the instance and its DAGs without source text or previous findings."""
        return self._context(
            AirflowContextOptions(
                include_source=False,
                include_previous=False,
                include_summary=False,
            ),
            dag_row_ids=None,
            candidate_table_ids=None,
        )

    def build_context(
        self,
        options: AirflowContextOptions | None = None,
        dag_row_ids: set[UUID] | None = None,
        candidate_table_ids: set[UUID] | None = None,
    ) -> AirflowAnalysisContext:
        """Analyze the given DAGs. None means every synced DAG of this instance."""
        selected: AirflowContextOptions = options or AirflowContextOptions()
        loaded: AirflowCatalogContext = self._context(selected, dag_row_ids, candidate_table_ids)
        if self._graph_builder is None:
            return AirflowAnalysisContext(
                instance_id=loaded.instance_id,
                instance_name=loaded.instance_name,
                summary=None,
                findings=[],
                dags_analyzed=len(loaded.dags),
                previous_links_kept=0,
            )
        written: AirflowAnalysisContext = self._graph_builder.apply(
            loaded,
            include_summary=selected.include_summary,
        )
        if written.instance_id != self._instance_id:
            raise ValidationAppError(
                "catalog context is for a different airflow instance",
                field="instance_id",
            )
        return written

    def describe(self) -> str:
        """Ask the model for the instance's business context and store it."""
        if self._describer is None or self._context_writer is None:
            raise LlmUnavailableError("context generation is not configured")
        loaded: AirflowCatalogContext = self._context(
            AirflowContextOptions(
                include_source=False,
                include_previous=True,
                include_summary=False,
            ),
            dag_row_ids=None,
            candidate_table_ids=None,
        )
        proposed: AirflowContextProposal = self._describer.propose(loaded)
        self._context_writer.save_airflow_instance(
            self._instance_id,
            proposed.context,
            ContextOrigin.LLM,
        )
        return proposed.context

    def _context(
        self,
        options: AirflowContextOptions,
        dag_row_ids: set[UUID] | None,
        candidate_table_ids: set[UUID] | None,
    ) -> AirflowCatalogContext:
        loaded: AirflowCatalogContext = self._catalog.get_airflow_context(
            self._instance_id,
            dag_row_ids,
            candidate_table_ids,
            options,
        )
        if loaded.instance_id != self._instance_id:
            raise ValidationAppError(
                "catalog context is for a different airflow instance",
                field="instance_id",
            )
        return loaded
