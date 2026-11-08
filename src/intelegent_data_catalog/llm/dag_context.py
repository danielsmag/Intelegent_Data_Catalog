"""Structured model call that writes business context for one Airflow DAG."""

from langchain_core.language_models.chat_models import BaseChatModel

from intelegent_data_catalog.agents.models import DagDescribeInput, FileContextProposal
from intelegent_data_catalog.llm.agent import ProposalAgent
from intelegent_data_catalog.llm.run import GraphCall
from intelegent_data_catalog.llm.spec import AgentSpec
from intelegent_data_catalog.llm.types import ThinkingLevel

_INSTRUCTIONS: str = (
    "You write business context for one Airflow DAG. "
    "The user message is JSON with the DAG id, description, task ids, and source. "
    "Return two to four sentences on what the DAG does. "
    "Put that text in context and a single sentence in summary. "
    "Do not invent tasks or tables the source does not name."
)


class DagContextAgent(ProposalAgent[DagDescribeInput, FileContextProposal]):
    """Asks the model for one DAG's business context."""

    def __init__(
        self,
        model: BaseChatModel,
        *,
        max_requests: int,
        temperature: float,
        max_tokens: int,
        timeout_seconds: float,
        thinking: ThinkingLevel = False,
    ) -> None:
        self._call: GraphCall[DagDescribeInput, FileContextProposal] = GraphCall(
            _agent(model),
            name="dag-context",
            max_requests=max_requests,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            thinking=thinking,
        )

    def propose(self, context: DagDescribeInput) -> FileContextProposal:
        """Return the context the model wrote for the DAG."""
        return self._call.run(context.model_dump_json(exclude_none=True), context)


def _agent(model: BaseChatModel) -> AgentSpec[DagDescribeInput, FileContextProposal]:
    return AgentSpec(
        model,
        output_type=FileContextProposal,
        instructions=_INSTRUCTIONS,
        retries=2,
        approve_output=True,
    )
