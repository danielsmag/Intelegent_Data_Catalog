"""Logical manager for one catalog scope. Holds no chat history."""

from uuid import UUID

from intelegent_data_catalog.exceptions import ValidationAppError
from intelegent_data_catalog.knowledge.context.scope_catalog import ScopeCatalogContextProvider
from intelegent_data_catalog.knowledge.context.scope_graph import ScopeGraphBuilder
from intelegent_data_catalog.knowledge.models import (
    ScopeAnalysisContext,
    ScopeCatalogContext,
    ScopeContextOptions,
)


class ScopeAgent:
    """Load one scope and ask for cross-table links and a summary.

    Create it through ``ScopeAgentFactory`` and drop it when the call finishes.
    PostgreSQL keeps the catalog. A missing graph builder stores metadata only.
    """

    def __init__(
        self,
        scope_id: UUID,
        catalog: ScopeCatalogContextProvider,
        graph_builder: ScopeGraphBuilder | None = None,
    ) -> None:
        self._scope_id: UUID = scope_id
        self._catalog: ScopeCatalogContextProvider = catalog
        self._graph_builder: ScopeGraphBuilder | None = graph_builder

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
            )
        written: ScopeAnalysisContext = self._graph_builder.apply(
            loaded,
            include_summary=selected.include_summary,
        )
        if written.scope.id != self._scope_id:
            raise ValidationAppError("catalog context is for a different scope", field="scope_id")
        return written

    def _context(self, options: ScopeContextOptions) -> ScopeCatalogContext:
        loaded: ScopeCatalogContext = self._catalog.get_scope_context(self._scope_id, options)
        if loaded.scope.id != self._scope_id:
            raise ValidationAppError("catalog context is for a different scope", field="scope_id")
        return loaded
