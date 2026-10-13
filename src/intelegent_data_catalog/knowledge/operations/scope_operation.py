"""Logical manager for one catalog scope. Holds no chat history."""

from typing import Protocol
from uuid import UUID

from intelegent_data_catalog.exceptions import LlmUnavailableError, ValidationAppError
from intelegent_data_catalog.knowledge.context.scope_catalog import ScopeCatalogContextProvider
from intelegent_data_catalog.knowledge.context.scope_graph import ScopeGraphAppender
from intelegent_data_catalog.knowledge.models import (
    ScopeAnalysisContext,
    ScopeCatalogContext,
    ScopeContextOptions,
    ScopeContextProposal,
    pipeline_summaries,
)
from intelegent_data_catalog.llm.agent import ProposalAgent
from intelegent_data_catalog.persistence.enums import ContextOrigin


class ScopeContextWriter(Protocol):
    """Stores business context on a scope row."""

    def save_scope(self, scope_id: UUID, text: str, origin: ContextOrigin) -> None:
        """Replace the scope context."""
        ...


class ScopeAgent:
    """Load one scope and ask for cross-table links and a summary.

    Create it through ``ScopeAgentFactory`` and drop it when the call finishes.
    PostgreSQL keeps the catalog. A missing graph builder stores metadata only.
    ``describe`` asks the model for business context and stores it as ``LLM``.
    """

    def __init__(
        self,
        scope_id: UUID,
        catalog: ScopeCatalogContextProvider,
        graph_builder: ScopeGraphAppender | None = None,
        describer: ProposalAgent[ScopeCatalogContext, ScopeContextProposal] | None = None,
        context_writer: ScopeContextWriter | None = None,
    ) -> None:
        self._scope_id: UUID = scope_id
        self._catalog: ScopeCatalogContextProvider = catalog
        self._graph_builder: ScopeGraphAppender | None = graph_builder
        self._describer: ProposalAgent[ScopeCatalogContext, ScopeContextProposal] | None = describer
        self._context_writer: ScopeContextWriter | None = context_writer

    @property
    def scope_id(self) -> UUID:
        """Catalog id this agent was created for."""
        return self._scope_id

    def get_metadata(self) -> ScopeCatalogContext:
        """Load the scope, its tables, and catalog relationships."""
        return self._context(
            ScopeContextOptions(
                include_table_knowledge=False,
                include_previous=False,
                include_summary=False,
                include_airflow=False,
            )
        )

    def build_context(self, options: ScopeContextOptions | None = None) -> ScopeAnalysisContext:
        """Combine scope metadata with links and a summary when a builder is configured."""
        selected: ScopeContextOptions = options or ScopeContextOptions()
        loaded: ScopeCatalogContext = self._context(selected)
        if self._graph_builder is None:
            return ScopeAnalysisContext(
                scope=loaded.scope,
                space=loaded.space,
                tables=loaded.tables,
                existing_relationships=loaded.relationships,
                table_links=[],
                summary=None,
                tables_without_context=list(loaded.tables_without_context),
                previous_links_kept=0,
                pipelines=pipeline_summaries(loaded.pipelines),
            )
        written: ScopeAnalysisContext = self._graph_builder.apply(
            loaded,
            include_summary=selected.include_summary,
        )
        if written.scope.id != self._scope_id:
            raise ValidationAppError("catalog context is for a different scope", field="scope_id")
        return written

    def describe(self) -> str:
        """Ask the model for the scope's business context and store it."""
        if self._describer is None or self._context_writer is None:
            raise LlmUnavailableError("context generation is not configured")
        loaded: ScopeCatalogContext = self._context(
            ScopeContextOptions(
                include_table_knowledge=False,
                include_previous=True,
                include_summary=False,
            )
        )
        proposed: ScopeContextProposal = self._describer.propose(loaded)
        self._context_writer.save_scope(self._scope_id, proposed.context, ContextOrigin.LLM)
        return proposed.context

    def _context(self, options: ScopeContextOptions) -> ScopeCatalogContext:
        loaded: ScopeCatalogContext = self._catalog.get_scope_context(self._scope_id, options)
        if loaded.scope.id != self._scope_id:
            raise ValidationAppError("catalog context is for a different scope", field="scope_id")
        return loaded
