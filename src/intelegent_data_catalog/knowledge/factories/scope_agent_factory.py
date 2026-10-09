"""Create a scope agent for one catalog id. The factory keeps no per-scope cache."""

from uuid import UUID

from intelegent_data_catalog.knowledge.context.scope_catalog import ScopeCatalogContextProvider
from intelegent_data_catalog.knowledge.context.scope_graph import ScopeGraphBuilder
from intelegent_data_catalog.knowledge.operations.scope_operation import ScopeAgent


class ScopeAgentFactory:
    """Inject the scope catalog and an optional graph builder."""

    def __init__(
        self,
        catalog: ScopeCatalogContextProvider,
        graph_builder: ScopeGraphBuilder | None = None,
    ) -> None:
        self._catalog: ScopeCatalogContextProvider = catalog
        self._graph_builder: ScopeGraphBuilder | None = graph_builder

    def create(self, scope_id: UUID) -> ScopeAgent:
        """Return a manager for ``scope_id``. Discard it after the calls finish."""
        return ScopeAgent(
            scope_id=scope_id,
            catalog=self._catalog,
            graph_builder=self._graph_builder,
        )


def build_scope_agent_factory(
    catalog: ScopeCatalogContextProvider,
    graph_builder: ScopeGraphBuilder | None = None,
) -> ScopeAgentFactory:
    """Wire the scope catalog and the builder that writes links.

    Without ``graph_builder``, ``build_context`` returns metadata and no links.
    """
    return ScopeAgentFactory(catalog=catalog, graph_builder=graph_builder)
