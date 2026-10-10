"""Create a space agent for one catalog id. The factory keeps no per-space cache."""

from uuid import UUID

from intelegent_data_catalog.knowledge.context.space_catalog import SpaceCatalogContextProvider
from intelegent_data_catalog.knowledge.context.space_graph import SpaceGraphAppender
from intelegent_data_catalog.knowledge.models import SpaceCatalogContext, SpaceContextProposal
from intelegent_data_catalog.knowledge.operations.space_operation import (
    SpaceAgent,
    SpaceContextWriter,
)
from intelegent_data_catalog.llm.agent import ProposalAgent

SpaceDescriber = ProposalAgent[SpaceCatalogContext, SpaceContextProposal]


class SpaceAgentFactory:
    """Inject the space catalog, an optional graph builder, and an optional describer."""

    def __init__(
        self,
        catalog: SpaceCatalogContextProvider,
        graph_builder: SpaceGraphAppender | None = None,
        describer: SpaceDescriber | None = None,
        context_writer: SpaceContextWriter | None = None,
    ) -> None:
        self._catalog: SpaceCatalogContextProvider = catalog
        self._graph_builder: SpaceGraphAppender | None = graph_builder
        self._describer: SpaceDescriber | None = describer
        self._context_writer: SpaceContextWriter | None = context_writer

    def create(self, space_id: UUID) -> SpaceAgent:
        """Return a manager for ``space_id``. Discard it after the calls finish."""
        return SpaceAgent(
            space_id=space_id,
            catalog=self._catalog,
            graph_builder=self._graph_builder,
            describer=self._describer,
            context_writer=self._context_writer,
        )


def build_space_agent_factory(
    catalog: SpaceCatalogContextProvider,
    graph_builder: SpaceGraphAppender | None = None,
    describer: SpaceDescriber | None = None,
    context_writer: SpaceContextWriter | None = None,
) -> SpaceAgentFactory:
    """Wire the space catalog, the builder that writes links, and the context describer.

    Without ``graph_builder``, ``build_context`` returns metadata and no links.
    Without ``describer``, ``describe`` fails until a model is configured.
    """
    return SpaceAgentFactory(
        catalog=catalog,
        graph_builder=graph_builder,
        describer=describer,
        context_writer=context_writer,
    )
