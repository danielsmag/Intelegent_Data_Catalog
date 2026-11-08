"""One model run. Catalog agents share this graph.

``ProposalAgent`` and ``TableModelAgent`` in ``llm.agent`` are the contracts.
``GraphCall`` is the run loop the concrete agents share.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Annotated, TypedDict, TypeIs, cast
from uuid import uuid4

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolCall,
    ToolMessage,
)
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langgraph.runtime import Runtime
from langgraph.types import Command, interrupt
from pydantic import BaseModel, ValidationError

from intelegent_data_catalog.core.logger import LOGGER_NAME
from intelegent_data_catalog.exceptions import ApprovalRequired, LlmError, LlmUnavailableError
from intelegent_data_catalog.llm.callbacks import llm_callbacks
from intelegent_data_catalog.llm.checkpoints import active_checkpointer
from intelegent_data_catalog.llm.errors import ToolRetry
from intelegent_data_catalog.llm.memory_tools import MemoryTools
from intelegent_data_catalog.llm.overlay import current_instructions, current_memory, current_task
from intelegent_data_catalog.llm.session import (
    approval_required,
    current_job_thread,
    current_resume,
    durable_runs,
)
from intelegent_data_catalog.llm.spec import AgentSpec
from intelegent_data_catalog.llm.structured import structured_method
from intelegent_data_catalog.llm.tool_deps import bind_tool_deps, reset_tool_deps
from intelegent_data_catalog.llm.trace import (
    AgentTrace,
    AgentTraceCallback,
    RunUsage,
    usage_from,
)
from intelegent_data_catalog.llm.types import StructuredMethod, ThinkingLevel

logger: logging.Logger = logging.getLogger(f"{LOGGER_NAME}.llm")

_ERROR_LIMIT: int = 300
_OUTPUT_TOOL: str = "submit_output"
_APPROVAL_TOOLS: frozenset[str] = frozenset({"publish_graph"})


def _merge_instructions(instructions: str | None) -> str | None:
    """Keep caller instructions and append the agent pack when a describe is active."""
    extra: str | None = current_instructions()
    if extra is None or extra.strip() == "":
        return instructions
    if instructions is None or instructions.strip() == "":
        return extra
    return f"{instructions.rstrip()}\n\n{extra.strip()}"


class GraphState(TypedDict, total=False):
    """Checkpointed run. Deps stay on the runtime context, not in this state."""

    messages: Annotated[list[BaseMessage], add_messages]
    requests: int
    retries: int
    output_text: str
    approval: dict[str, object]


@dataclass
class CallContext:
    """Deps for one invocation. A checkpoint cannot store them."""

    deps: object


class GraphCall[DepsT, OutputT]:
    """Runs one LangGraph agent: limits, trace, checkpoints, and error mapping.

    Callers keep their own prompt, tools, and output type.
    """

    def __init__(
        self,
        spec: AgentSpec[DepsT, OutputT],
        *,
        name: str,
        max_requests: int,
        temperature: float,
        max_tokens: int,
        timeout_seconds: float,
        thinking: ThinkingLevel | None = None,
        require_approval: bool = False,
        checkpointer: BaseCheckpointSaver[str] | None = None,
    ) -> None:
        self._spec: AgentSpec[DepsT, OutputT] = spec
        self._name: str = name
        self._max_requests: int = max_requests
        self._timeout_seconds: float = timeout_seconds
        self._require_approval: bool = require_approval
        self._checkpointer: BaseCheckpointSaver[str] | None = checkpointer
        self._thread: str | None = None
        self._limit: int = max_requests
        bounded: int = _bounded_max_tokens(spec.model, max_tokens)
        stored_thinking: ThinkingLevel = False if thinking is None else thinking
        self._settings: dict[str, object] = {
            "temperature": temperature,
            "max_tokens": bounded,
            "timeout": timeout_seconds,
            "thinking": stored_thinking,
        }

    def run(
        self,
        prompt: str,
        deps: DepsT,
        instructions: str | None = None,
    ) -> OutputT:
        """Run until the model finishes, pauses for approval, or hits a limit."""
        sent: str = prompt
        task: str | None = current_task()
        if task is not None and '"task"' not in prompt:
            sent = f"{prompt}\n\nTask:\n{task}"
        return asyncio.run(
            self._execute(
                prompt=sent,
                deps=deps,
                instructions=_merge_instructions(instructions),
            )
        )

    async def _execute(
        self,
        prompt: str,
        deps: DepsT,
        instructions: str | None,
    ) -> OutputT:
        trace: AgentTrace = AgentTrace(self._name)
        trace.start(prompt)
        memory: MemoryTools | None = current_memory()
        self._limit = self._max_requests if memory is None else self._max_requests + 2
        if memory is not None:
            note: str = await memory.preamble()
            if note != "":
                if instructions is None:
                    instructions = note
                else:
                    instructions = f"{instructions.rstrip()}\n\n{note}"
        try:
            result: dict[str, object] = await asyncio.wait_for(
                self._invoke(prompt, deps, instructions, memory, trace),
                timeout=self._timeout_seconds,
            )
        except TimeoutError as err:
            seconds: int = int(self._timeout_seconds)
            raise LlmError(f"language model timed out after {seconds} seconds") from err
        except ApprovalRequired:
            raise
        except LlmError:
            raise
        except Exception as err:
            mapped: LlmError | None = _provider_error(self._name, err)
            if mapped is None:
                raise
            raise mapped from err
        paused: object | None = _interrupt_value(result)
        if paused is not None:
            raise ApprovalRequired(_payload(paused))
        output_text: object = result.get("output_text")
        if not isinstance(output_text, str):
            raise LlmError("language model returned an unexpected response")
        messages: list[BaseMessage] = _messages(result.get("messages"))
        requests: object = result.get("requests", 0)
        request_count: int = requests if isinstance(requests, int) else 0
        usage: RunUsage = usage_from(messages, request_count)
        trace.finish(messages, usage)
        output: OutputT = _decode(self._spec.output_type, output_text)
        trace.emit_output(output)
        return output

    async def _invoke(
        self,
        prompt: str,
        deps: DepsT,
        instructions: str | None,
        memory: MemoryTools | None,
        trace: AgentTrace,
    ) -> dict[str, object]:
        saver: BaseCheckpointSaver[str] | None = self._saver()
        graph: object = self._compile(memory, trace, saver)
        thread: str = self._thread_id()
        config: dict[str, object] = {
            "configurable": {"thread_id": thread},
            "callbacks": [*llm_callbacks(), AgentTraceCallback(trace)],
        }
        context: CallContext = CallContext(deps)
        resume: dict[str, object] | None = current_resume()
        snapshot: object | None = None
        if saver is not None:
            snapshot = await graph.aget_state(config)  # type: ignore[attr-defined]
        pending: object | None = _snapshot_interrupt(snapshot)
        if pending is not None and resume is None:
            raise ApprovalRequired(_payload(pending))
        if pending is not None and resume is not None:
            raw: object = await graph.ainvoke(  # type: ignore[attr-defined]
                Command(resume=resume),
                config,
                context=context,
            )
            return _as_dict(raw)
        if snapshot is not None and _snapshot_next(snapshot):
            raw = await graph.ainvoke(None, config, context=context)  # type: ignore[attr-defined]
            return _as_dict(raw)
        raw = await graph.ainvoke(  # type: ignore[attr-defined]
            _initial(prompt, instructions),
            config,
            context=context,
        )
        return _as_dict(raw)

    def _compile(
        self,
        memory: MemoryTools | None,
        trace: AgentTrace,
        saver: BaseCheckpointSaver[str] | None,
    ) -> object:
        tools: list[BaseTool] = list(self._spec.tools)
        if memory is not None:
            tools.extend(memory.tools())
        structured: bool = self._spec.output_type is not str
        method: StructuredMethod = structured_method(self._spec.model)
        output_model: type[BaseModel] | None = _model_type(self._spec.output_type)
        if structured and method == "function_calling" and output_model is not None:
            tools.append(_output_tool(output_model))
        builder: StateGraph[GraphState, CallContext, GraphState, GraphState] = StateGraph(
            GraphState,
            context_schema=CallContext,
        )
        _add_node(builder, "llm", self._llm_node(tools, method, trace))
        _add_node(builder, "gate", self._gate_node())
        _add_node(builder, "tools", self._tools_node(tools, trace))
        _add_node(builder, "reject", _reject_node)
        _add_node(builder, "validate", self._validate_node(trace))
        _add_node(builder, "finish", _finish_node)
        builder.add_edge(START, "llm")
        builder.add_conditional_edges(
            "llm",
            lambda state: _after_llm(state, structured=structured),
            {"gate": "gate", "validate": "validate", "finish": "finish"},
        )
        builder.add_conditional_edges(
            "gate",
            _after_gate,
            {"tools": "tools", "reject": "reject"},
        )
        builder.add_edge("tools", "llm")
        builder.add_edge("reject", "llm")
        builder.add_conditional_edges(
            "validate",
            _after_validate,
            {"llm": "llm", "finish": "finish"},
        )
        builder.add_edge("finish", END)
        if saver is None:
            return builder.compile()
        return builder.compile(checkpointer=saver)

    def _llm_node(
        self,
        tools: list[BaseTool],
        method: StructuredMethod,
        trace: AgentTrace,
    ) -> object:
        async def llm(state: GraphState, runtime: Runtime[CallContext]) -> dict[str, object]:
            del runtime
            requests: int = state.get("requests", 0)
            if requests >= self._limit:
                raise LlmError("language model exceeded the tool step limit")
            message: AIMessage = await _call_model(
                self._spec.model,
                list(state.get("messages", [])),
                tools,
                self._settings,
                method if self._spec.output_type is not str else None,
                _model_type(self._spec.output_type),
            )
            return {"messages": [message], "requests": requests + 1}

        return llm

    def _gate_node(self) -> object:
        def gate(state: GraphState) -> dict[str, object]:
            if not _needs_approval(state, enabled=self._approval_enabled()):
                return {"approval": {"decision": "approve"}}
            decision: object = interrupt(_approval_payload(state))
            return {"approval": _decision(decision)}

        return gate

    def _tools_node(self, tools: list[BaseTool], trace: AgentTrace) -> object:
        async def tools_node(
            state: GraphState,
            runtime: Runtime[CallContext],
        ) -> dict[str, object]:
            last: BaseMessage | None = _last(state)
            if isinstance(last, AIMessage):
                call: ToolCall
                for call in last.tool_calls:
                    name: object = call.get("name", "tool")
                    trace.observe_tool_call(str(name), call.get("args", {}))
            token = bind_tool_deps(runtime.context.deps)
            try:
                node: ToolNode = ToolNode(tools, handle_tool_errors=_tool_error)
                raw: object = await node.ainvoke(state)
            finally:
                reset_tool_deps(token)
            result: dict[str, object] = _as_dict(raw)
            produced: list[BaseMessage] = _messages(result.get("messages"))
            produced_message: BaseMessage
            for produced_message in produced:
                if not isinstance(produced_message, ToolMessage):
                    continue
                tool_name: str = produced_message.name or "tool"
                if produced_message.status == "error":
                    trace.observe_retry(tool_name, produced_message.content)
                else:
                    trace.observe_tool_result(tool_name, produced_message.content)
            return result

        return tools_node

    def _validate_node(self, trace: AgentTrace) -> object:
        def validate(state: GraphState, runtime: Runtime[CallContext]) -> dict[str, object]:
            if self._spec.output_type is str:
                return {}
            schema: type[BaseModel] | None = _model_type(self._spec.output_type)
            if schema is None:
                return {}
            last: BaseMessage | None = _last(state)
            if not isinstance(last, AIMessage):
                raise LlmError("language model returned an unexpected response")
            try:
                parsed_model: BaseModel = _parse_model(schema, last)
                parsed: OutputT = cast(OutputT, parsed_model)
                validator = self._spec.validator
                if validator is not None:
                    parsed = validator(cast(DepsT, runtime.context.deps), parsed)
            except (ValidationError, ToolRetry, ValueError) as err:
                if state.get("retries", 0) >= self._spec.retries:
                    raise LlmError(_rejected(self._name, err)) from err
                detail: str = _with_cause(err)
                trace.observe_retry("model", detail)
                return {
                    "messages": [
                        HumanMessage(content=detail, additional_kwargs={"retry": True}),
                    ],
                    "retries": state.get("retries", 0) + 1,
                }
            if self._spec.approve_output and self._approval_enabled():
                proposed: dict[str, object] = {}
                if isinstance(parsed, BaseModel):
                    dumped: object = parsed.model_dump(mode="json")
                    if isinstance(dumped, dict):
                        proposed = cast(dict[str, object], dumped)
                decision: object = interrupt({"kind": "output", "output": proposed})
                chosen: dict[str, object] = _decision(decision)
                if chosen.get("decision") == "reject":
                    raise LlmError("proposal rejected")
                edited: object = chosen.get("output")
                if isinstance(edited, dict):
                    parsed = cast(OutputT, schema.model_validate(edited))
            if isinstance(parsed, BaseModel):
                return {"output_text": parsed.model_dump_json()}
            return {"output_text": str(parsed)}

        return validate

    def _approval_enabled(self) -> bool:
        return self._require_approval or approval_required()

    def _saver(self) -> BaseCheckpointSaver[str] | None:
        if self._checkpointer is not None:
            return self._checkpointer
        if self._approval_enabled() or durable_runs():
            found: BaseCheckpointSaver[str] | None = active_checkpointer()
            if found is None and self._approval_enabled():
                raise LlmUnavailableError("approval requires durable graph checkpoints")
            return found
        return None

    def _thread_id(self) -> str:
        job_thread: str | None = current_job_thread(self._name)
        if job_thread is not None:
            return job_thread
        if self._thread is None:
            self._thread = str(uuid4())
        return self._thread


def _add_node(
    builder: StateGraph[GraphState, CallContext, GraphState, GraphState],
    name: str,
    action: object,
) -> None:
    """Register a node. LangGraph's overloads do not describe these closures."""
    builder.add_node(name, action)  # type: ignore[call-overload]


def _initial(prompt: str, instructions: str | None) -> GraphState:
    messages: list[BaseMessage] = []
    if instructions is not None and instructions.strip() != "":
        messages.append(SystemMessage(content=instructions))
    messages.append(HumanMessage(content=prompt))
    return {"messages": messages, "requests": 0, "retries": 0}


def _after_llm(state: GraphState, *, structured: bool) -> str:
    last: BaseMessage | None = _last(state)
    if isinstance(last, AIMessage) and _action_calls(last):
        return "gate"
    if structured:
        return "validate"
    return "finish"


def _after_gate(state: GraphState) -> str:
    approval: dict[str, object] = state.get("approval", {})
    if approval.get("decision") == "reject":
        return "reject"
    return "tools"


def _after_validate(state: GraphState) -> str:
    if isinstance(state.get("output_text"), str):
        return "finish"
    return "llm"


def _finish_node(state: GraphState) -> dict[str, object]:
    if isinstance(state.get("output_text"), str):
        return {}
    last: BaseMessage | None = _last(state)
    if isinstance(last, AIMessage):
        return {"output_text": _message_text(last)}
    raise LlmError("language model returned an unexpected response")


def _reject_node(state: GraphState) -> dict[str, object]:
    last: BaseMessage | None = _last(state)
    messages: list[ToolMessage] = []
    if isinstance(last, AIMessage):
        call: ToolCall
        for call in last.tool_calls:
            call_id: object = call.get("id", "")
            name: object = call.get("name", "tool")
            messages.append(
                ToolMessage(
                    content="rejected by reviewer",
                    tool_call_id=str(call_id),
                    name=str(name),
                    status="error",
                )
            )
    return {"messages": messages, "approval": {"decision": "approve"}}


def _needs_approval(state: GraphState, *, enabled: bool) -> bool:
    if not enabled:
        return False
    last: BaseMessage | None = _last(state)
    if not isinstance(last, AIMessage):
        return False
    guarded: bool = any(
        str(call.get("name", "")) in _APPROVAL_TOOLS for call in last.tool_calls
    )
    return guarded


def _approval_payload(state: GraphState) -> dict[str, object]:
    last: BaseMessage | None = _last(state)
    tools: list[dict[str, object]] = []
    if isinstance(last, AIMessage):
        call: ToolCall
        for call in last.tool_calls:
            if str(call.get("name", "")) in _APPROVAL_TOOLS:
                tools.append({"name": str(call.get("name", "")), "args": call.get("args", {})})
    return {"kind": "tool", "tools": tools}


def _decision(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        copied: dict[str, object] = {}
        key: object
        item: object
        for key, item in value.items():
            copied[str(key)] = item
        if "decision" not in copied:
            copied["decision"] = "approve"
        return copied
    if value == "approve":
        return {"decision": "approve"}
    if value == "reject":
        return {"decision": "reject"}
    return {"decision": "approve"}


def _action_calls(message: AIMessage) -> bool:
    if len(message.tool_calls) == 0:
        return False
    actionable: bool = any(
        str(call.get("name", "")) != _OUTPUT_TOOL for call in message.tool_calls
    )
    return actionable


async def _call_model(
    model: object,
    messages: list[BaseMessage],
    tools: list[BaseTool],
    settings: dict[str, object],
    method: StructuredMethod | None,
    schema: type[BaseModel] | None,
) -> AIMessage:
    from langchain_anthropic import ChatAnthropic
    from langchain_ollama import ChatOllama
    from langchain_openai import ChatOpenAI

    runnable: object = model
    if len(tools) > 0 and hasattr(model, "bind_tools"):
        runnable = model.bind_tools(tools)
    extra: dict[str, object] = {
        "temperature": settings["temperature"],
    }
    max_tokens: object = settings["max_tokens"]
    if isinstance(model, ChatOllama):
        extra["num_predict"] = max_tokens
    else:
        extra["max_tokens"] = max_tokens
    thinking: object = settings.get("thinking")
    if isinstance(thinking, str) and thinking != "off":
        if isinstance(model, ChatOpenAI) and not _model_label(model).startswith("gpt-4.1"):
            extra["reasoning_effort"] = thinking
        elif isinstance(model, ChatAnthropic):
            extra["thinking"] = {"type": "enabled", "budget_tokens": _thinking_budget(thinking)}
        elif isinstance(model, ChatOllama):
            extra["reasoning"] = True
    if method == "json_schema" and schema is not None and isinstance(model, ChatOpenAI):
        extra["response_format"] = _json_schema(schema)
    if method == "json_schema" and isinstance(model, ChatOllama):
        extra["format"] = "json"
    if hasattr(runnable, "bind"):
        runnable = runnable.bind(**extra)
    produced: object = await runnable.ainvoke(messages)  # type: ignore[attr-defined]
    if isinstance(produced, AIMessage):
        return produced
    raise LlmError("language model returned an unexpected response")


def _parse_model[OutputT: BaseModel](schema: type[OutputT], message: AIMessage) -> OutputT:
    call: ToolCall
    for call in message.tool_calls:
        if str(call.get("name", "")) != _OUTPUT_TOOL:
            continue
        args: object = call.get("args", {})
        if isinstance(args, str):
            return schema.model_validate_json(args)
        if isinstance(args, dict):
            return schema.model_validate(args)
    text: str = _json_text(_message_text(message))
    if text == "":
        raise ToolRetry("model returned an empty answer")
    return schema.model_validate_json(text)


def _decode[OutputT](schema: type[OutputT], text: str) -> OutputT:
    if schema is str:
        return cast(OutputT, text)
    if _is_model(schema):
        return cast(OutputT, schema.model_validate_json(text))
    raise LlmError("language model returned an unexpected response")


def _output_tool(schema: type[BaseModel]) -> BaseTool:
    def submit_output(**kwargs: object) -> str:
        del kwargs
        return "ok"

    return StructuredTool.from_function(
        func=submit_output,
        name=_OUTPUT_TOOL,
        description="Return the finished structured answer.",
        args_schema=schema,
    )


def _tool_error(error: Exception) -> str:
    return str(error)


def _json_schema(schema: type[BaseModel]) -> dict[str, object]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": schema.__name__,
            "schema": schema.model_json_schema(),
        },
    }


def _json_text(content: str) -> str:
    text: str = content.strip()
    if not text.startswith("```"):
        return text
    lines: list[str] = text.splitlines()
    if len(lines) >= 2 and lines[-1].startswith("```"):
        lines = lines[1:-1]
    return "\n".join(lines).strip()


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
            elif isinstance(block, dict):
                text: object = block.get("text", "")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return str(content)


def _last(state: GraphState) -> BaseMessage | None:
    messages: list[BaseMessage] = list(state.get("messages", []))
    if len(messages) == 0:
        return None
    return messages[-1]


def _messages(value: object) -> list[BaseMessage]:
    if not isinstance(value, list):
        return []
    found: list[BaseMessage] = []
    item: object
    for item in value:
        if isinstance(item, BaseMessage):
            found.append(item)
    return found


def _as_dict(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return cast(dict[str, object], value)
    raise LlmError("language model returned an unexpected response")


def _interrupt_value(result: dict[str, object]) -> object | None:
    raw: object = result.get("__interrupt__")
    if not isinstance(raw, list) or len(raw) == 0:
        return None
    first: object = raw[0]
    value: object = getattr(first, "value", first)
    return value


def _snapshot_interrupt(snapshot: object | None) -> object | None:
    if snapshot is None:
        return None
    tasks: object = getattr(snapshot, "tasks", ())
    if not isinstance(tasks, tuple) and not isinstance(tasks, list):
        return None
    task: object
    for task in tasks:
        interrupts: object = getattr(task, "interrupts", ())
        if isinstance(interrupts, tuple | list):
            item: object
            for item in interrupts:
                return getattr(item, "value", item)
    return None


def _snapshot_next(snapshot: object) -> bool:
    nxt: object = getattr(snapshot, "next", ())
    return isinstance(nxt, tuple) and len(nxt) > 0


def _payload(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        payload: dict[str, object] = {}
        key: object
        item: object
        for key, item in value.items():
            payload[str(key)] = item
        return payload
    return {"kind": "output", "output": value}


def _model_type(schema: object) -> type[BaseModel] | None:
    if isinstance(schema, type) and issubclass(schema, BaseModel):
        return schema
    return None


def _is_model(schema: object) -> TypeIs[type[BaseModel]]:
    return isinstance(schema, type) and issubclass(schema, BaseModel)


def _model_label(model: object) -> str:
    named: object = getattr(model, "model_name", None)
    if isinstance(named, str) and named != "":
        return named
    raw: object = getattr(model, "model", None)
    if isinstance(raw, str):
        return raw
    return ""


def _thinking_budget(effort: str) -> int:
    budgets: dict[str, int] = {
        "minimal": 1024,
        "low": 2048,
        "medium": 8000,
        "high": 16000,
        "xhigh": 32000,
    }
    return budgets.get(effort, 2048)


_GPT_4_1_COMPLETION_LIMIT: int = 32768
_CLAUDE_OUTPUT_CAP: int = 64000


def completion_limit(provider: str, model_name: str) -> int | None:
    """Return the completion cap the provider accepts, or none when it is open."""
    if model_name == "gpt-4.1" or model_name.startswith("gpt-4.1-"):
        return _GPT_4_1_COMPLETION_LIMIT
    if provider == "claude":
        return _CLAUDE_OUTPUT_CAP
    return None


def _bounded_max_tokens(model: object, requested: int) -> int:
    """Keep the completion budget inside the limit the provider accepts."""
    provider: str = "claude" if "anthropic" in type(model).__module__ else ""
    limit: int | None = completion_limit(provider, _model_label(model))
    if limit is None or requested <= limit:
        return requested
    return limit


def _provider_error(name: str, err: BaseException) -> LlmError | None:
    """Map a provider HTTP failure onto ``LlmError``. Other errors stay untouched."""
    import httpx
    from anthropic import APIStatusError as AnthropicStatusError
    from openai import APIStatusError as OpenAIStatusError

    status: int | None = None
    body: object = None
    if isinstance(err, OpenAIStatusError | AnthropicStatusError):
        status = err.status_code
        body = err.body
    elif isinstance(err, httpx.HTTPStatusError):
        status = err.response.status_code
        body = err.response.text
    elif isinstance(err, httpx.RequestError):
        return LlmError("language model is unavailable")
    if status is None:
        return None
    logger.warning(
        "agent=%s language model returned %s: %s",
        name,
        status,
        _provider_message(body),
    )
    return LlmError(f"language model returned {status}")


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
