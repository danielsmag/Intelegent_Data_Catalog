"""Log one model run: the ask, each tool step, and the final output."""

import json
import logging
from collections.abc import Sequence
from typing import TypeVar

from pydantic import BaseModel
from pydantic_ai.messages import (
    ModelMessage,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.run import AgentRunResult
from pydantic_ai.usage import RunUsage

from intelegent_data_catalog.core.logger import LOGGER_NAME

_INFO_LIMIT: int = 500

logger: logging.Logger = logging.getLogger(f"{LOGGER_NAME}.llm")

OutputT = TypeVar("OutputT")


class AgentTrace:
    """Writes one agent run to the package logger.

    The ask is logged before the model runs. Tool calls, tool results, retries,
    and the final output are logged after ``all_messages`` is available.
    """

    def __init__(self, agent_name: str) -> None:
        self._agent_name: str = agent_name

    def start(self, ask: str) -> None:
        """Log the instruction or prompt before the model runs."""
        self._emit("ask", ask)

    def finish(self, result: AgentRunResult[OutputT]) -> None:
        """Log tool steps, the model output, and token usage."""
        saw_text: bool = False
        message: ModelMessage
        for message in result.all_messages():
            for part in message.parts:
                if isinstance(part, TextPart):
                    saw_text = True
                self._log_part(part)
        output: OutputT = result.output
        if not isinstance(output, str) or not saw_text:
            self._emit("output", _render(output))
        usage: RunUsage = result.usage
        logger.info(
            "agent=%s usage requests=%s tool_calls=%s input_tokens=%s output_tokens=%s",
            self._agent_name,
            usage.requests,
            usage.tool_calls,
            usage.input_tokens,
            usage.output_tokens,
        )

    def _log_part(self, part: object) -> None:
        if isinstance(part, UserPromptPart):
            return
        if isinstance(part, ToolCallPart):
            rendered_args: str = _render(part.args)
            self._emit("tool_call", f"{part.tool_name} {rendered_args}")
            return
        if isinstance(part, ToolReturnPart):
            rendered_content: str = _render(part.content)
            self._emit("tool_result", f"{part.tool_name} {rendered_content}")
            return
        if isinstance(part, RetryPromptPart):
            tool_name: str = part.tool_name or "model"
            rendered_retry: str = _render(part.content)
            self._emit("retry", f"{tool_name} {rendered_retry}")
            return
        if isinstance(part, TextPart):
            self._emit("text", part.content)

    def _emit(self, kind: str, body: str) -> None:
        shown: str = body if len(body) <= _INFO_LIMIT else f"{body[:_INFO_LIMIT]}..."
        logger.info("agent=%s %s=%s", self._agent_name, kind, shown)
        if len(body) > _INFO_LIMIT:
            logger.debug("agent=%s %s=%s", self._agent_name, kind, body)


def _render(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, BaseModel):
        return value.model_dump_json()
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        items: list[object] = []
        item: object
        for item in value:
            if isinstance(item, BaseModel):
                items.append(item.model_dump(mode="json"))
            else:
                items.append(item)
        return json.dumps(items, default=str)
    try:
        encoded: str = json.dumps(value, default=str)
    except TypeError:
        return str(value)
    return encoded
