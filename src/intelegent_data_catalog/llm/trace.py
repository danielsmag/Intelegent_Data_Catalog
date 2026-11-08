"""Log one model run: the ask, each tool step, and the final output."""

import json
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import LLMResult
from pydantic import BaseModel

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


@dataclass
class RunUsage:
    """Token totals collected from the model messages in one run."""

    requests: int
    tool_calls: int
    input_tokens: int
    output_tokens: int


class AgentTrace:
    """Writes one agent run to the package logger and the build trace.

    The ask is logged before the model runs. Live tool steps are logged as the
    graph yields them. The final output and token usage are logged after.
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

    def finish(self, messages: list[BaseMessage], usage: RunUsage) -> None:
        """Log remaining text, the model output, and token usage."""
        publish_parts: bool = not self._streamed
        message: BaseMessage
        for message in messages:
            if not isinstance(message, AIMessage):
                continue
            text: str = _message_text(message)
            if text != "":
                self._emit("text", text, publish=publish_parts)
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

    def emit_output(self, output: object) -> None:
        """Log the value returned to the caller."""
        self._emit("output", _render(output), publish=True)

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


class AgentTraceCallback(BaseCallbackHandler):
    """Forwards LangChain tool events into ``AgentTrace``."""

    def __init__(self, trace: AgentTrace) -> None:
        super().__init__()
        self._trace: AgentTrace = trace

    def on_tool_start(
        self,
        serialized: dict[str, object],
        input_str: str,
        *,
        inputs: dict[str, object] | None = None,
        **kwargs: object,
    ) -> None:
        name: object = serialized.get("name", "tool")
        body: object = inputs if inputs is not None else input_str
        self._trace.observe_tool_call(str(name), body)

    def on_tool_end(self, output: object, **kwargs: object) -> None:
        name: object = kwargs.get("name", "tool")
        self._trace.observe_tool_result(str(name), output)

    def on_tool_error(self, error: BaseException, **kwargs: object) -> None:
        name: object = kwargs.get("name", "tool")
        self._trace.observe_retry(str(name), error)

    def on_llm_end(self, response: LLMResult, **kwargs: object) -> None:
        del response, kwargs


def usage_from(messages: list[BaseMessage], requests: int) -> RunUsage:
    """Sum token metadata the chat model attached to its replies."""
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: int = 0
    message: BaseMessage
    for message in messages:
        if isinstance(message, AIMessage):
            tool_calls += len(message.tool_calls)
            metadata: object = message.usage_metadata
            if isinstance(metadata, dict):
                incoming: object = metadata.get("input_tokens", 0)
                outgoing: object = metadata.get("output_tokens", 0)
                if isinstance(incoming, int):
                    input_tokens += incoming
                if isinstance(outgoing, int):
                    output_tokens += outgoing
        if isinstance(message, ToolMessage):
            continue
    return RunUsage(
        requests=requests,
        tool_calls=tool_calls,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def _message_text(message: AIMessage) -> str:
    content: object = message.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        block: object
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                text: object = block.get("text", "")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return str(content)


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
