"""Static configuration for one compiled model graph."""

from collections.abc import Callable

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import BaseTool


class AgentSpec[DepsT, OutputT]:
    """Tools, schema, and checks for one ``GraphCall``. The graph is built later."""

    def __init__(
        self,
        model: BaseChatModel,
        *,
        output_type: type[OutputT],
        tools: list[BaseTool] | None = None,
        instructions: str | None = None,
        retries: int = 1,
        validator: Callable[[DepsT, OutputT], OutputT] | None = None,
        approve_output: bool = False,
    ) -> None:
        self.model: BaseChatModel = model
        self.output_type: type[OutputT] = output_type
        self.tools: list[BaseTool] = [] if tools is None else tools
        self.instructions: str | None = instructions
        self.retries: int = retries
        self.validator: Callable[[DepsT, OutputT], OutputT] | None = validator
        self.approve_output: bool = approve_output
