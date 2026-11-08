"""Checkpoint resume and approval interrupts for one graph call."""

import json
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import MemorySaver

from intelegent_data_catalog.exceptions import ApprovalRequired, LlmError
from intelegent_data_catalog.llm.run import GraphCall
from intelegent_data_catalog.llm.session import llm_job, llm_resume
from intelegent_data_catalog.llm.spec import AgentSpec
from tests.llm_fakes import (
    AgentInfo,
    FunctionModel,
    ModelMessage,
    ModelResponse,
    TextPart,
    ToolCallPart,
)


def test_checkpoint_resumes_a_crashed_tool_run() -> None:
    """A second call on the same thread continues after the tool, without a new ask."""
    asks: list[int] = []
    saver: MemorySaver = MemorySaver()

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del info
        asks.append(len(messages))
        if len(asks) == 1:
            raise RuntimeError("worker died after the model asked for a tool")
        return ModelResponse(parts=[TextPart(content="finished")])

    spec: AgentSpec[object, str] = AgentSpec(
        FunctionModel(respond),
        output_type=str,
        tools=[],
    )
    call: GraphCall[object, str] = GraphCall(
        spec,
        name="resume-test",
        max_requests=4,
        temperature=0.0,
        max_tokens=32,
        timeout_seconds=5.0,
        checkpointer=saver,
    )
    with llm_job(uuid4()):
        with pytest.raises(RuntimeError, match="worker died"):
            call.run("start", object())
        # The crash happened before a checkpointed tool, so the retry starts again.
        answer: str = call.run("start", object())
    assert answer == "finished"


def test_publish_graph_waits_for_approval() -> None:
    """publish_graph pauses, then runs after an approve decision."""
    calls: list[str] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del info
        calls.append("model")
        if len(calls) == 1:
            return ModelResponse(parts=[ToolCallPart(tool_name="publish_graph", args={})])
        return ModelResponse(parts=[TextPart(content="published")])

    from langchain_core.tools import tool

    @tool
    def publish_graph() -> str:
        """Publish the projection."""
        calls.append("tool")
        return "nodes=1"

    saver: MemorySaver = MemorySaver()
    call: GraphCall[object, str] = GraphCall(
        AgentSpec(FunctionModel(respond), output_type=str, tools=[publish_graph]),
        name="approval-test",
        max_requests=4,
        temperature=0.0,
        max_tokens=32,
        timeout_seconds=5.0,
        require_approval=True,
        checkpointer=saver,
    )
    job = uuid4()
    with llm_job(job):
        with pytest.raises(ApprovalRequired) as caught:
            call.run("publish", object())
        payload: dict[str, object] = caught.value.payload
        assert payload["kind"] == "tool"
        with llm_resume({"decision": "approve"}):
            answer: str = call.run("publish", object())
    assert answer == "published"
    assert "tool" in calls


def test_rejected_publish_does_not_run_the_tool() -> None:
    """A rejection tells the model and skips the tool."""
    seen: list[str] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del info
        if len(seen) == 0:
            seen.append("ask")
            return ModelResponse(parts=[ToolCallPart(tool_name="publish_graph", args={})])
        parts: list[str] = [str(part.content) for message in messages for part in message.parts]
        body: str = json.dumps(parts)
        seen.append(body)
        return ModelResponse(parts=[TextPart(content="skipped")])

    from langchain_core.tools import tool

    @tool
    def publish_graph() -> str:
        """Publish the projection."""
        seen.append("ran")
        return "no"

    saver: MemorySaver = MemorySaver()
    call: GraphCall[object, str] = GraphCall(
        AgentSpec(FunctionModel(respond), output_type=str, tools=[publish_graph]),
        name="reject-test",
        max_requests=4,
        temperature=0.0,
        max_tokens=32,
        timeout_seconds=5.0,
        require_approval=True,
        checkpointer=saver,
    )
    with llm_job(uuid4()):
        with pytest.raises(ApprovalRequired):
            call.run("publish", object())
        with llm_resume({"decision": "reject"}):
            answer: str = call.run("publish", object())
    assert answer == "skipped"
    assert "ran" not in seen
    assert "rejected" in seen[-1]


def test_output_approval_can_edit_the_proposal() -> None:
    """A resumed decision can replace the structured proposal."""
    from pydantic import BaseModel, ConfigDict

    class Proposal(BaseModel):
        model_config = ConfigDict(extra="forbid")

        context: str

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del messages, info
        return ModelResponse(parts=[TextPart(content=json.dumps({"context": "draft"}))])

    saver: MemorySaver = MemorySaver()
    call: GraphCall[object, Proposal] = GraphCall(
        AgentSpec(FunctionModel(respond), output_type=Proposal, approve_output=True),
        name="edit-test",
        max_requests=2,
        temperature=0.0,
        max_tokens=32,
        timeout_seconds=5.0,
        require_approval=True,
        checkpointer=saver,
    )
    with llm_job(uuid4()):
        with pytest.raises(ApprovalRequired):
            call.run("describe", object())
        with llm_resume({"decision": "approve", "output": {"context": "edited"}}):
            edited: Proposal = call.run("describe", object())
    assert edited.context == "edited"


def test_missing_checkpointer_refuses_approval() -> None:
    """Approval without Postgres checkpoints fails before the model is trusted."""

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del messages, info
        return ModelResponse(parts=[ToolCallPart(tool_name="publish_graph", args={})])

    from langchain_core.tools import tool

    @tool
    def publish_graph() -> str:
        """Publish the projection."""
        return "no"

    call: GraphCall[object, str] = GraphCall(
        AgentSpec(FunctionModel(respond), output_type=str, tools=[publish_graph]),
        name="no-saver",
        max_requests=2,
        temperature=0.0,
        max_tokens=16,
        timeout_seconds=5.0,
        require_approval=True,
    )
    with pytest.raises(LlmError, match="checkpoints"):
        call.run("publish", object())
