"""One model run. Table, column, and scope agents share this skeleton.

``ProposalAgent`` and ``TableModelAgent`` in ``llm.agent`` are the contracts.
``ModelCall`` is the run loop the concrete agents share.
"""

from __future__ import annotations

import asyncio
import logging

from pydantic_ai import Agent
from pydantic_ai.exceptions import (
    ModelAPIError,
    ModelHTTPError,
    ToolRetryError,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
    UserError,
)
from pydantic_ai.run import AgentRunResult
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import UsageLimits

from intelegent_data_catalog.core.logger import LOGGER_NAME
from intelegent_data_catalog.exceptions import LlmError, LlmUnavailableError
from intelegent_data_catalog.llm.trace import AgentTrace

logger: logging.Logger = logging.getLogger(f"{LOGGER_NAME}.llm")

_ERROR_LIMIT: int = 300


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
        thinking: bool | None = None,
    ) -> None:
        self._agent: Agent[DepsT, OutputT] = agent
        self._name: str = name
        self._max_requests: int = max_requests
        settings: ModelSettings = {
            "temperature": temperature,
            "max_tokens": max_tokens,
            "timeout": timeout_seconds,
        }
        if thinking is not None:
            settings["thinking"] = thinking
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
        result: AgentRunResult[OutputT]
        try:
            if instructions is None:
                result = await self._agent.run(
                    prompt,
                    deps=deps,
                    model_settings=self._settings,
                    usage_limits=UsageLimits(request_limit=self._max_requests),
                )
            else:
                result = await self._agent.run(
                    prompt,
                    deps=deps,
                    instructions=instructions,
                    model_settings=self._settings,
                    usage_limits=UsageLimits(request_limit=self._max_requests),
                )
        except UsageLimitExceeded as err:
            raise LlmError("language model exceeded the tool step limit") from err
        except ModelHTTPError as err:
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
