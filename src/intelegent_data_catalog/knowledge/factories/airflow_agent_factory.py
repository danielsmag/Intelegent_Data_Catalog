"""Create an Airflow agent for one instance. The factory keeps no per-instance cache."""

from uuid import UUID

from intelegent_data_catalog.knowledge.context.airflow_catalog import AirflowCatalogContextProvider
from intelegent_data_catalog.knowledge.context.airflow_graph import AirflowGraphAppender
from intelegent_data_catalog.knowledge.models import AirflowCatalogContext, AirflowContextProposal
from intelegent_data_catalog.knowledge.operations.airflow_operation import (
    AirflowContextWriter,
    AirflowInstanceAgent,
)
from intelegent_data_catalog.llm.agent import ProposalAgent

AirflowDescriber = ProposalAgent[AirflowCatalogContext, AirflowContextProposal]


class AirflowAgentFactory:
    """Inject the Airflow catalog, an optional graph builder, and an optional describer."""

    def __init__(
        self,
        catalog: AirflowCatalogContextProvider,
        graph_builder: AirflowGraphAppender | None = None,
        describer: AirflowDescriber | None = None,
        context_writer: AirflowContextWriter | None = None,
    ) -> None:
        self._catalog: AirflowCatalogContextProvider = catalog
        self._graph_builder: AirflowGraphAppender | None = graph_builder
        self._describer: AirflowDescriber | None = describer
        self._context_writer: AirflowContextWriter | None = context_writer

    def create(self, instance_id: UUID) -> AirflowInstanceAgent:
        """Return a manager for ``instance_id``. Discard it after the calls finish."""
        return AirflowInstanceAgent(
            instance_id=instance_id,
            catalog=self._catalog,
            graph_builder=self._graph_builder,
            describer=self._describer,
            context_writer=self._context_writer,
        )


def build_airflow_agent_factory(
    catalog: AirflowCatalogContextProvider,
    graph_builder: AirflowGraphAppender | None = None,
    describer: AirflowDescriber | None = None,
    context_writer: AirflowContextWriter | None = None,
) -> AirflowAgentFactory:
    """Wire the Airflow catalog, the builder that writes DAG links, and the describer.

    Without ``graph_builder``, ``build_context`` returns metadata and no findings.
    Without ``describer``, ``describe`` fails until a model is configured.
    """
    return AirflowAgentFactory(
        catalog=catalog,
        graph_builder=graph_builder,
        describer=describer,
        context_writer=context_writer,
    )
