"""Log one model run: the ask, each tool step, and the final output."""

import json
import logging
import time
from collections.abc import Sequence
from typing import TypeVar

from pydantic import BaseModel
from pydantic_ai.messages import (
    ModelMessage,
    RetryFeedbackPart,
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
_STEPS: dict[str, str] = {
    "ask": "ask",
    "tool_call": "tool_call",
    "tool_result": "tool_result",
    "retry": "retry",
    "text": "text",
    "output": "output",
    "usage": "usage",
}

logger: logging.Logger = logging.getLogger(f"{LOGGER_NAME}.llm")

OutputT = TypeVar("OutputT")


class AgentTrace:
    """Writes one agent run to the package logger and the build trace.

    The ask is logged before the model runs. Live tool steps are logged as the
    event stream yields them. The final output and token usage are logged after.
    """

    def __init__(self, agent_name: str) -> None:
        self._agent_name: str = agent_name
        self._streamed: bool = False
        self._seen: set[tuple[str, str]] = set()
        self._started_at: float = time.monotonic()

    def start(self, ask: str) -> None:
        """Log the instruction or prompt before the model runs."""
        self._started_at = time.monotonic()
        self._emit("ask", ask, publish=True)

    def observe_tool_call(self, name: str, args: object) -> None:
        """Log one tool call as the model requests it."""
        self._streamed = True
        rendered_args: str = _render(args)
        self._emit("tool_call", f"{name} {rendered_args}", publish=True)

    def observe_tool_result(self, name: str, content: object) -> None:
        """Log one tool result as the tool returns."""
        self._streamed = True
        rendered_content: str = _render(content)
        self._emit("tool_result", f"{name} {rendered_content}", publish=True)

    def observe_retry(self, name: str, content: object) -> None:
        """Log one tool retry as the model is asked to try again."""
        self._streamed = True
        rendered_retry: str = _render(content)
        self._emit("retry", f"{name} {rendered_retry}", publish=True)

    def finish(self, result: AgentRunResult[OutputT]) -> None:
        """Log remaining steps, the model output, and token usage."""
        saw_text: bool = False
        publish_parts: bool = not self._streamed
        message: ModelMessage
        for message in result.all_messages():
            for part in message.parts:
                if isinstance(part, TextPart):
                    saw_text = True
                self._log_part(part, publish=publish_parts)
        output: OutputT = result.output
        if not isinstance(output, str) or not saw_text or self._streamed:
            self._emit("output", _render(output), publish=True)
        usage: RunUsage = result.usage
        logger.info(
            "agent=%s usage requests=%s tool_calls=%s input_tokens=%s output_tokens=%s",
            self._agent_name,
            usage.requests,
            usage.tool_calls,
            usage.input_tokens,
            usage.output_tokens,
        )
        elapsed_seconds: float = time.monotonic() - self._started_at
        usage_detail: str = _usage_detail(usage.input_tokens, usage.output_tokens, elapsed_seconds)
        self._emit("usage", usage_detail, publish=True, log=False)

    def _log_part(self, part: object, *, publish: bool) -> None:
        if isinstance(part, UserPromptPart):
            return
        if isinstance(part, ToolCallPart):
            rendered_args: str = _render(part.args)
            self._emit("tool_call", f"{part.tool_name} {rendered_args}", publish=publish)
            return
        if isinstance(part, ToolReturnPart):
            rendered_content: str = _render(part.content)
            self._emit("tool_result", f"{part.tool_name} {rendered_content}", publish=publish)
            return
        if isinstance(part, RetryFeedbackPart):
            rendered_feedback: str = _render(part.content)
            self._emit("retry", f"model {rendered_feedback}", publish=publish)
            return
        if isinstance(part, RetryPromptPart):
            tool_name: str = part.tool_name or "model"
            rendered_retry: str = _render(part.content)
            self._emit("retry", f"{tool_name} {rendered_retry}", publish=publish)
            return
        if isinstance(part, TextPart):
            self._emit("text", part.content, publish=publish)

    def _emit(self, kind: str, body: str, *, publish: bool, log: bool = True) -> None:
        seen_key: tuple[str, str] = (kind, body)
        if seen_key in self._seen:
            return
        self._seen.add(seen_key)
        if log:
            shown: str = body if len(body) <= _INFO_LIMIT else f"{body[:_INFO_LIMIT]}..."
            logger.info("agent=%s %s=%s", self._agent_name, kind, shown)
            if len(body) > _INFO_LIMIT:
                logger.debug("agent=%s %s=%s", self._agent_name, kind, body)
        if not publish:
            return
        step: str | None = _STEPS.get(kind)
        if step is None:
            return
        from intelegent_data_catalog.jobs.trace import publish_build_step

        publish_build_step(self._agent_name, step, body)  # type: ignore[arg-type]


def _usage_detail(input_tokens: int, output_tokens: int, elapsed_seconds: float) -> str:
    span: str = _format_span(elapsed_seconds)
    return f"{input_tokens:,} in · {output_tokens:,} out · {span}"


def _format_span(elapsed_seconds: float) -> str:
    seconds: int = max(0, round(elapsed_seconds))
    if seconds == 0:
        return "<1s"
    if seconds < 60:
        return f"{seconds}s"
    minutes: int = seconds // 60
    rest: int = seconds % 60
    if rest == 0:
        return f"{minutes}m"
    return f"{minutes}m {rest}s"


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
