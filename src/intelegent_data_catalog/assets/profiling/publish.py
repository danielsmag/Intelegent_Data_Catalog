"""Store profile markdown and replace the asset's Neo4j subgraph."""

import logging

from pydantic import JsonValue

from intelegent_data_catalog.assets.dsl.operators.base import OperatorContext
from intelegent_data_catalog.assets.dsl.operators.ports import ProfileGraphWriter
from intelegent_data_catalog.assets.profiling.graph import ProfileGraphBuilder
from intelegent_data_catalog.assets.profiling.markdown import ProfileMarkdown
from intelegent_data_catalog.assets.profiling.models import ProfileDocument, ProfileGraphStatus
from intelegent_data_catalog.exceptions import GraphUnavailableError


class ProfilePublisher:
    """Writes the profile output. A graph failure stays on the document."""

    def __init__(self, context: OperatorContext) -> None:
        self._context: OperatorContext = context
        self._markdown: ProfileMarkdown = ProfileMarkdown()
        self._graphs: ProfileGraphBuilder = ProfileGraphBuilder()

    def publish(self, document: ProfileDocument) -> None:
        """Record the graph outcome, then store markdown and JSON."""
        recorded: ProfileDocument = self._with_graph(document)
        payload: JsonValue = recorded.model_dump(mode="json")
        self._context.write_output("profile", self._markdown.render(recorded), payload)

    def _with_graph(self, document: ProfileDocument) -> ProfileDocument:
        writer: ProfileGraphWriter | None = self._context.profile_graph()
        if writer is None:
            return document.model_copy(
                update={
                    "graph": ProfileGraphStatus(
                        written=False,
                        error="graph projection is not configured",
                    ),
                },
            )
        try:
            writer.replace(self._graphs.build(self._context.asset_id, document))
        except GraphUnavailableError as err:
            logger: logging.Logger = self._context.logger
            logger.warning(
                "profile graph write failed asset_id=%s error=%s",
                self._context.asset_id,
                err,
            )
            return document.model_copy(
                update={"graph": ProfileGraphStatus(written=False, error=str(err))},
            )
        return document.model_copy(update={"graph": ProfileGraphStatus(written=True, error=None)})
