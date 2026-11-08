"""Scripted chat model. Tests return text or tool calls without a provider."""

import json
from collections.abc import Awaitable, Callable
from typing import cast

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import BaseTool
from pydantic import PrivateAttr


class UserPromptPart:
    """One user message, shaped like the old model transcript."""

    def __init__(self, content: str) -> None:
        self.content: str = content


class TextPart:
    """One text reply."""

    def __init__(self, content: str) -> None:
        self.content: str = content


class ToolCallPart:
    """One tool call."""

    def __init__(
        self,
        tool_name: str,
        args: dict[str, object] | str,
        tool_call_id: str | None = None,
    ) -> None:
        self.tool_name: str = tool_name
        self.args: dict[str, object] | str = args
        self.tool_call_id: str | None = tool_call_id

    def args_as_json_str(self) -> str:
        if isinstance(self.args, str):
            return self.args
        return json.dumps(self.args)


class ToolReturnPart:
    """One tool result."""

    def __init__(self, content: object, tool_name: str = "") -> None:
        self.content: object = content
        self.tool_name: str = tool_name


class RetryPromptPart:
    """One validation retry sent back to the model."""

    def __init__(self, content: object, tool_name: str | None = None) -> None:
        self.content: object = content
        self.tool_name: str | None = tool_name


class RetryFeedbackPart:
    """Alias kept so older assertions can name the same retry."""

    def __init__(self, content: object) -> None:
        self.content: object = content


class ModelMessage:
    """One transcript message made of parts."""

    def __init__(self, parts: list[object]) -> None:
        self.parts: list[object] = parts


class ModelResponse:
    """One scripted model reply."""

    def __init__(self, parts: list[object]) -> None:
        self.parts: list[object] = parts


class _ToolInfo:
    def __init__(self, name: str) -> None:
        self.name: str = name


class AgentInfo:
    """Tools the graph bound for this call."""

    def __init__(self, function_tools: list[_ToolInfo]) -> None:
        self.function_tools: list[_ToolInfo] = function_tools


_Function = Callable[[list[ModelMessage], AgentInfo], ModelResponse | Awaitable[ModelResponse]]


class FunctionModel(BaseChatModel):
    """Calls a test function with the transcript seen so far."""

    _function: _Function = PrivateAttr()

    def __init__(self, function: _Function) -> None:
        super().__init__()
        self._function = function

    @property
    def _llm_type(self) -> str:
        return "function"

    def bind_tools(
        self,
        tools: list[object],
        *,
        tool_choice: str | None = None,
        **kwargs: object,
    ) -> object:
        del tool_choice
        return self.bind(tools=tools, **kwargs)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: object = None,
        **kwargs: object,
    ) -> ChatResult:
        del stop, run_manager
        info: AgentInfo = AgentInfo(_tool_names(kwargs.get("tools")))
        produced: ModelResponse | Awaitable[ModelResponse] = self._function(_adapt(messages), info)
        if not isinstance(produced, ModelResponse):
            raise TypeError("scripted model replies must be synchronous")
        message: AIMessage = _to_ai(produced)
        return ChatResult(generations=[ChatGeneration(message=message)])

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: object = None,
        **kwargs: object,
    ) -> ChatResult:
        return self._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


class HangingModel(BaseChatModel):
    """Never returns. The graph timeout must cut it off."""

    @property
    def _llm_type(self) -> str:
        return "hanging"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: object = None,
        **kwargs: object,
    ) -> ChatResult:
        del messages, stop, run_manager, kwargs
        import time

        time.sleep(30)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=""))])

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: object = None,
        **kwargs: object,
    ) -> ChatResult:
        del messages, stop, run_manager, kwargs
        import asyncio

        await asyncio.sleep(30)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=""))])


def _adapt(messages: list[BaseMessage]) -> list[ModelMessage]:
    adapted: list[ModelMessage] = []
    message: BaseMessage
    for message in messages:
        if isinstance(message, HumanMessage):
            if message.additional_kwargs.get("retry") is True:
                adapted.append(ModelMessage([RetryPromptPart(content=message.content)]))
            else:
                adapted.append(ModelMessage([UserPromptPart(content=_text(message.content))]))
            continue
        if isinstance(message, ToolMessage):
            adapted.append(
                ModelMessage(
                    [ToolReturnPart(content=message.content, tool_name=message.name or "")],
                )
            )
    return adapted


def _to_ai(response: ModelResponse) -> AIMessage:
    text: list[str] = []
    tool_calls: list[dict[str, object]] = []
    part: object
    for part in response.parts:
        if isinstance(part, TextPart) and part.content != "":
            text.append(part.content)
            continue
        if isinstance(part, ToolCallPart):
            args: object = part.args
            if isinstance(args, str):
                parsed: object = json.loads(args)
                args = parsed if isinstance(parsed, dict) else {}
            if not isinstance(args, dict):
                args = {}
            tool_calls.append(
                {
                    "name": part.tool_name,
                    "args": args,
                    "id": part.tool_call_id or f"call-{len(tool_calls)}",
                    "type": "tool_call",
                }
            )
    return AIMessage(content="".join(text), tool_calls=cast(list[object], tool_calls))


def _tool_names(tools: object) -> list[_ToolInfo]:
    if not isinstance(tools, list):
        return []
    names: list[_ToolInfo] = []
    tool: object
    for tool in tools:
        if isinstance(tool, BaseTool):
            names.append(_ToolInfo(tool.name))
            continue
        if isinstance(tool, dict):
            function: object = tool.get("function", tool)
            if isinstance(function, dict):
                raw: object = function.get("name", "")
                if isinstance(raw, str) and raw != "":
                    names.append(_ToolInfo(raw))
    return names


def _text(content: object) -> str:
    if isinstance(content, str):
        return content
    return str(content)
