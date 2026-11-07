"""Structured model call that writes business context for one Airflow DAG."""

from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.settings import ThinkingLevel

from intelegent_data_catalog.agents.models import DagDescribeInput, FileContextProposal
from intelegent_data_catalog.llm.agent import ProposalAgent
from intelegent_data_catalog.llm.run import ModelCall
from intelegent_data_catalog.llm.structured import structured_output

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
        model: Model,
        *,
        max_requests: int,
        temperature: float,
        max_tokens: int,
        timeout_seconds: float,
        thinking: ThinkingLevel = False,
    ) -> None:
        self._call: ModelCall[DagDescribeInput, FileContextProposal] = ModelCall(
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


def _agent(model: Model) -> Agent[DagDescribeInput, FileContextProposal]:
    agent: Agent[DagDescribeInput, FileContextProposal] = Agent(
        model,
        deps_type=DagDescribeInput,
        output_type=structured_output(model, FileContextProposal),
        name="dag-context",
        instructions=_INSTRUCTIONS,
        retries=2,
    )
    return agent
