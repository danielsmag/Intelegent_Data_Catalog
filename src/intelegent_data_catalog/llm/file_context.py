"""Structured model call that writes business context for a file or notebook."""

from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import BaseModel, ConfigDict

from intelegent_data_catalog.agents.models import FileCatalogContext, FileContextProposal
from intelegent_data_catalog.llm.agent import ProposalAgent
from intelegent_data_catalog.llm.overlay import current_task
from intelegent_data_catalog.llm.run import GraphCall
from intelegent_data_catalog.llm.spec import AgentSpec
from intelegent_data_catalog.llm.types import ThinkingLevel

_INSTRUCTIONS: str = (
    "You write business context for one workspace file or notebook. "
    "The user message is JSON with the file name and extracted text. "
    "Return two to four sentences in context and one sentence in summary. "
    "Do not invent tables or pipelines the text does not mention."
)
_TEXT_LIMIT: int = 8_000


class _FilePrompt(BaseModel):
    """User message for one file. Long text is clipped."""

    model_config = ConfigDict(extra="forbid")

    name: str
    kind: str
    text: str
    referenced_tables: list[str]
    task: str | None = None


class FileContextAgent(ProposalAgent[FileCatalogContext, FileContextProposal]):
    """Asks the model for one file's business context."""

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
        self._call: GraphCall[FileCatalogContext, FileContextProposal] = GraphCall(
            _agent(model),
            name="file-context",
            max_requests=max_requests,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            thinking=thinking,
        )

    def propose(self, context: FileCatalogContext) -> FileContextProposal:
        """Return the context the model wrote for the file."""
        prompt: _FilePrompt = _FilePrompt(
            name=context.name,
            kind=context.kind.value,
            text=context.text[:_TEXT_LIMIT],
            referenced_tables=context.referenced_tables,
            task=current_task(),
        )
        return self._call.run(prompt.model_dump_json(exclude_none=True), context)


def _agent(model: BaseChatModel) -> AgentSpec[FileCatalogContext, FileContextProposal]:
    return AgentSpec(
        model,
        output_type=FileContextProposal,
        instructions=_INSTRUCTIONS,
        retries=2,
        approve_output=True,
    )
