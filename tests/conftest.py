"""Pytest defaults. Eager Celery runs builds in-process so Redis is not required."""

import os
from collections.abc import AsyncIterator, Awaitable, Callable

from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel

os.environ["CATALOG_CELERY__TASK_ALWAYS_EAGER"] = "true"

_Function = Callable[[list[ModelMessage], AgentInfo], ModelResponse | Awaitable[ModelResponse]]
_Stream = Callable[[list[ModelMessage], AgentInfo], AsyncIterator[str | DeltaToolCalls]]


def _stream_from(function: _Function) -> _Stream:
    """Replay a non-streaming test reply as text or tool-call deltas."""

    async def stream(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[str | DeltaToolCalls]:
        produced: ModelResponse | Awaitable[ModelResponse] = function(messages, info)
        response: ModelResponse = await produced if isinstance(produced, Awaitable) else produced
        sent: bool = False
        index: int
        part: object
        for index, part in enumerate(response.parts):
            if isinstance(part, TextPart) and part.content != "":
                sent = True
                yield part.content
                continue
            if isinstance(part, ToolCallPart):
                sent = True
                delta: DeltaToolCalls = {
                    index: DeltaToolCall(
                        name=part.tool_name,
                        json_args=part.args_as_json_str(),
                        tool_call_id=part.tool_call_id,
                    )
                }
                yield delta
        if not sent:
            yield ""

    return stream


def _install_default_stream() -> None:
    original: Callable[..., None] = FunctionModel.__init__

    def init(
        self: FunctionModel,
        function: _Function | None = None,
        *,
        stream_function: _Stream | None = None,
        model_name: str | None = None,
        profile: object = None,
        settings: object = None,
    ) -> None:
        chosen: _Stream | None = stream_function
        if function is not None and stream_function is None:
            chosen = _stream_from(function)
        original(
            self,
            function,
            stream_function=chosen,
            model_name=model_name,
            profile=profile,
            settings=settings,
        )

    FunctionModel.__init__ = init  # type: ignore[method-assign]


_install_default_stream()
