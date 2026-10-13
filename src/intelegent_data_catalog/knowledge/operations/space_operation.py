"""Logical manager for one catalog space. Holds no chat history."""

from typing import Protocol
from uuid import UUID

from intelegent_data_catalog.exceptions import LlmUnavailableError, ValidationAppError
from intelegent_data_catalog.knowledge.context.space_catalog import SpaceCatalogContextProvider
from intelegent_data_catalog.knowledge.context.space_graph import SpaceGraphAppender
from intelegent_data_catalog.knowledge.models import (
    SpaceAnalysisContext,
    SpaceCatalogContext,
    SpaceContextOptions,
    SpaceContextProposal,
    pipeline_summaries,
)
from intelegent_data_catalog.llm.agent import ProposalAgent
from intelegent_data_catalog.persistence.enums import ContextOrigin


class SpaceContextWriter(Protocol):
    """Stores business context on a space row."""

    def save_space(self, space_id: UUID, text: str, origin: ContextOrigin) -> None:
        """Replace the space context."""
        ...


class SpaceAgent:
    """Load one space and ask for cross-scope links and a summary.

    Create it through ``SpaceAgentFactory`` and drop it when the call finishes.
    PostgreSQL keeps the catalog. A missing graph builder stores metadata only.
    ``describe`` asks the model for business context and stores it as ``LLM``.
    """

    def __init__(
        self,
        space_id: UUID,
        catalog: SpaceCatalogContextProvider,
        graph_builder: SpaceGraphAppender | None = None,
        describer: ProposalAgent[SpaceCatalogContext, SpaceContextProposal] | None = None,
        context_writer: SpaceContextWriter | None = None,
    ) -> None:
        self._space_id: UUID = space_id
        self._catalog: SpaceCatalogContextProvider = catalog
        self._graph_builder: SpaceGraphAppender | None = graph_builder
        self._describer: ProposalAgent[SpaceCatalogContext, SpaceContextProposal] | None = describer
        self._context_writer: SpaceContextWriter | None = context_writer

    @property
    def space_id(self) -> UUID:
        """Catalog id this agent was created for."""
        return self._space_id

    def get_metadata(self) -> SpaceCatalogContext:
        """Load the space, its scopes, and cross-scope catalog relationships."""
        return self._context(
            SpaceContextOptions(
                include_scope_knowledge=False,
                include_previous=False,
                include_summary=False,
                include_airflow=False,
            )
        )

    def build_context(self, options: SpaceContextOptions | None = None) -> SpaceAnalysisContext:
        """Combine space metadata with links and a summary when a builder is configured."""
        selected: SpaceContextOptions = options or SpaceContextOptions()
        loaded: SpaceCatalogContext = self._context(selected)
        if self._graph_builder is None:
            return SpaceAnalysisContext(
                space=loaded.space,
                scopes=loaded.scopes,
                existing_relationships=loaded.relationships,
                table_links=[],
                summary=None,
                scopes_without_context=list(loaded.scopes_without_context),
                previous_links_kept=0,
                pipelines=pipeline_summaries(loaded.pipelines),
            )
        written: SpaceAnalysisContext = self._graph_builder.apply(
            loaded,
            include_summary=selected.include_summary,
        )
        if written.space.id != self._space_id:
            raise ValidationAppError("catalog context is for a different space", field="space_id")
        return written

    def describe(self) -> str:
        """Ask the model for the space's business context and store it."""
        if self._describer is None or self._context_writer is None:
            raise LlmUnavailableError("context generation is not configured")
        loaded: SpaceCatalogContext = self._context(
            SpaceContextOptions(
                include_scope_knowledge=False,
                include_previous=True,
                include_summary=False,
            )
        )
        proposed: SpaceContextProposal = self._describer.propose(loaded)
        self._context_writer.save_space(self._space_id, proposed.context, ContextOrigin.LLM)
        return proposed.context

    def _context(self, options: SpaceContextOptions) -> SpaceCatalogContext:
        loaded: SpaceCatalogContext = self._catalog.get_space_context(self._space_id, options)
        if loaded.space.id != self._space_id:
            raise ValidationAppError("catalog context is for a different space", field="space_id")
        return loaded
