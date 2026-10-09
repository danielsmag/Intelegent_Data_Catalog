"""Create a scope agent for one catalog id. The factory keeps no per-scope cache."""

from uuid import UUID

from intelegent_data_catalog.knowledge.context.scope_catalog import ScopeCatalogContextProvider
from intelegent_data_catalog.knowledge.context.scope_graph import ScopeGraphAppender
from intelegent_data_catalog.knowledge.models import ScopeCatalogContext, ScopeContextProposal
from intelegent_data_catalog.knowledge.operations.scope_operation import (
    ScopeAgent,
    ScopeContextWriter,
)
from intelegent_data_catalog.llm.agent import ProposalAgent

ScopeDescriber = ProposalAgent[ScopeCatalogContext, ScopeContextProposal]


class ScopeAgentFactory:
    """Inject the scope catalog, an optional graph builder, and an optional describer."""

    def __init__(
        self,
        catalog: ScopeCatalogContextProvider,
        graph_builder: ScopeGraphAppender | None = None,
        describer: ScopeDescriber | None = None,
        context_writer: ScopeContextWriter | None = None,
    ) -> None:
        self._catalog: ScopeCatalogContextProvider = catalog
        self._graph_builder: ScopeGraphAppender | None = graph_builder
        self._describer: ScopeDescriber | None = describer
        self._context_writer: ScopeContextWriter | None = context_writer

    def create(self, scope_id: UUID) -> ScopeAgent:
        """Return a manager for ``scope_id``. Discard it after the calls finish."""
        return ScopeAgent(
            scope_id=scope_id,
            catalog=self._catalog,
            graph_builder=self._graph_builder,
            describer=self._describer,
            context_writer=self._context_writer,
        )


def build_scope_agent_factory(
    catalog: ScopeCatalogContextProvider,
    graph_builder: ScopeGraphAppender | None = None,
    describer: ScopeDescriber | None = None,
    context_writer: ScopeContextWriter | None = None,
) -> ScopeAgentFactory:
    """Wire the scope catalog, the builder that writes links, and the context describer.

    Without ``graph_builder``, ``build_context`` returns metadata and no links.
    Without ``describer``, ``describe`` fails until a model is configured.
    """
    return ScopeAgentFactory(
        catalog=catalog,
        graph_builder=graph_builder,
        describer=describer,
        context_writer=context_writer,
    )
