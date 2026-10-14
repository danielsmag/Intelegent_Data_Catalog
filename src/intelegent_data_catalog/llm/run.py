"""One model run. Table, column, and scope agents share this skeleton.

``ProposalAgent`` and ``TableModelAgent`` in ``llm.agent`` are the contracts.
``ModelCall`` is the run loop the concrete agents share.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterable, Awaitable, Callable

from pydantic_ai import Agent, RunContext
from pydantic_ai.exceptions import (
    ModelAPIError,
    ModelHTTPError,
    ToolRetryError,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
    UserError,
)
from pydantic_ai.messages import (
    AgentStreamEvent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    RetryPromptPart,
    ToolReturnPart,
)
from pydantic_ai.models import Model
from pydantic_ai.run import AgentRunResult
from pydantic_ai.settings import ModelSettings, ThinkingLevel
from pydantic_ai.usage import UsageLimits

from intelegent_data_catalog.core.logger import LOGGER_NAME
from intelegent_data_catalog.exceptions import LlmError, LlmUnavailableError
from intelegent_data_catalog.llm.trace import AgentTrace

logger: logging.Logger = logging.getLogger(f"{LOGGER_NAME}.llm")

_ERROR_LIMIT: int = 300
_GPT_4_1_COMPLETION_LIMIT: int = 32768


class ModelCall[DepsT, OutputT]:
    """Runs one pydantic-ai agent: limits, trace, and error mapping.

    Callers keep their own prompt, tools, and output type.
    """

    def __init__(
        self,
        agent: Agent[DepsT, OutputT],
        *,
        name: str,
        max_requests: int,
        temperature: float,
        max_tokens: int,
        timeout_seconds: float,
        thinking: ThinkingLevel | None = None,
    ) -> None:
        self._agent: Agent[DepsT, OutputT] = agent
        self._name: str = name
        self._max_requests: int = max_requests
        bounded_tokens: int = _bounded_max_tokens(agent.model, max_tokens)
        settings: ModelSettings = {
            "temperature": temperature,
            "max_tokens": bounded_tokens,
            "timeout": timeout_seconds,
        }
        if thinking is not None:
            settings["thinking"] = thinking
        self._timeout_seconds: float = timeout_seconds
        self._settings: ModelSettings = settings

    def run(
        self,
        prompt: str,
        deps: DepsT,
        instructions: str | None = None,
    ) -> OutputT:
        """Run until the model finishes or the request limit is reached."""
        return asyncio.run(self._execute(prompt, deps, instructions))

    async def _execute(
        self,
        prompt: str,
        deps: DepsT,
        instructions: str | None,
    ) -> OutputT:
        trace: AgentTrace = AgentTrace(self._name)
        trace.start(prompt)

        async def handle_events(
            _ctx: RunContext[DepsT],
            events: AsyncIterable[AgentStreamEvent],
        ) -> None:
            event: AgentStreamEvent
            async for event in events:
                _observe_event(trace, event)

        result: AgentRunResult[OutputT]
        try:
            result = await asyncio.wait_for(
                self._invoke(prompt, deps, instructions, handle_events),
                timeout=self._timeout_seconds,
            )
        except TimeoutError as err:
            seconds: int = int(self._timeout_seconds)
            raise LlmError(f"language model timed out after {seconds} seconds") from err
        except UsageLimitExceeded as err:
            raise LlmError("language model exceeded the tool step limit") from err
        except ModelHTTPError as err:
            logger.warning(
                "agent=%s language model returned %s: %s",
                self._name,
                err.status_code,
                _provider_message(err.body),
            )
            raise LlmError(f"language model returned {err.status_code}") from err
        except ModelAPIError as err:
            detail: str = str(err).strip()
            if detail == "":
                detail = "language model is unavailable"
            raise LlmError(detail) from err
        except UnexpectedModelBehavior as err:
            raise LlmError(_rejected(self._name, err)) from err
        except ToolRetryError as err:
            raise LlmError(_rejected(self._name, err)) from err
        except UserError as err:
            raise LlmUnavailableError("language model is not configured") from err
        trace.finish(result)
        output: OutputT = result.output
        return output

    async def _invoke(
        self,
        prompt: str,
        deps: DepsT,
        instructions: str | None,
        handle_events: Callable[
            [RunContext[DepsT], AsyncIterable[AgentStreamEvent]],
            Awaitable[None],
        ],
    ) -> AgentRunResult[OutputT]:
        """Run one agent call. The caller applies the total timeout."""
        if instructions is None:
            return await self._agent.run(
                prompt,
                deps=deps,
                model_settings=self._settings,
                usage_limits=UsageLimits(request_limit=self._max_requests),
                event_stream_handler=handle_events,
            )
        return await self._agent.run(
            prompt,
            deps=deps,
            instructions=instructions,
            model_settings=self._settings,
            usage_limits=UsageLimits(request_limit=self._max_requests),
            event_stream_handler=handle_events,
        )


def _bounded_max_tokens(model: object, requested: int) -> int:
    """Keep the completion budget inside the limit the provider accepts.

    ``settings.toml`` asks for 100000 so a reasoning model can finish. GPT-4.1
    rejects anything above 32768, and Claude rejects a budget above the output
    cap on its profile.
    """
    limit: int | None = _completion_limit(model)
    if limit is None or requested <= limit:
        return requested
    return limit


def _completion_limit(model: object) -> int | None:
    if not isinstance(model, Model):
        return None
    name: str = model.model_name
    if name == "gpt-4.1" or name.startswith("gpt-4.1-"):
        return _GPT_4_1_COMPLETION_LIMIT
    cap: object = model.profile.get("anthropic_max_output_tokens")
    if isinstance(cap, int) and cap >= 1:
        return cap
    return None


def _provider_message(body: object) -> str:
    """Return the provider error text. The stored job error stays the status code."""
    message: object = ""
    if isinstance(body, dict):
        message = body.get("message", "")
        if not isinstance(message, str) or message.strip() == "":
            error: object = body.get("error")
            if isinstance(error, dict):
                message = error.get("message", "")
    elif body is not None:
        message = str(body)
    if not isinstance(message, str):
        return ""
    text: str = " ".join(message.split())
    return text[:_ERROR_LIMIT]


def _observe_event(trace: AgentTrace, event: AgentStreamEvent) -> None:
    """Publish a tool step as soon as the model stream yields it."""
    if isinstance(event, FunctionToolCallEvent):
        trace.observe_tool_call(event.part.tool_name, event.part.args)
        return
    if not isinstance(event, FunctionToolResultEvent):
        return
    part: ToolReturnPart | RetryPromptPart = event.part
    if isinstance(part, RetryPromptPart):
        tool_name: str = part.tool_name or "model"
        trace.observe_retry(tool_name, part.content)
        return
    trace.observe_tool_result(part.tool_name, part.content)


def _rejected(name: str, err: BaseException) -> str:
    """Keep the provider text. The alert shows a short form of the same message."""
    text: str = _with_cause(err)
    logger.warning("agent=%s rejected model output: %s", name, text)
    if text == "":
        return "language model returned an unexpected response"
    if len(text) <= _ERROR_LIMIT:
        return text
    return text[:_ERROR_LIMIT]


def _with_cause(err: BaseException) -> str:
    """Append the validation error hidden under the retry-limit wrapper."""
    text: str = " ".join(str(err).split())
    cause: BaseException | None = err.__cause__
    if cause is None:
        return text
    detail: str = " ".join(str(cause).split())
    if detail == "" or detail in text:
        return text
    if text == "":
        return detail
    return f"{text}: {detail}"
