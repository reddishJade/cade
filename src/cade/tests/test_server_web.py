"""Web 序列化与运行控制器的纯逻辑测试。"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from enum import StrEnum
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import BaseModel

from cade.agent.messages import AssistantMessage
from cade.agent.types import TextContent, ToolCallContent
from cade.harness.agent_runtime.events import (
    FinalStructuredEvent,
    MessageStartStructuredEvent,
)
from cade.harness.agent_runtime.result import AgentHarnessResult
from cade.server.runner import WebRunHub
from cade.server.serialize import event_to_dict, to_jsonable


class _Color(StrEnum):
    RED = "red"


class _PayloadModel(BaseModel):
    name: str = "m"
    count: int = 1


@dataclass(frozen=True)
class _Nested:
    value: str = "v"
    tags: tuple[str, ...] = ("a", "b")
    model: _PayloadModel = field(default_factory=_PayloadModel)
    color: _Color = _Color.RED


def test_to_jsonable_handles_enum_dataclass_and_pydantic() -> None:
    payload = to_jsonable(_Nested())
    assert payload == {
        "value": "v",
        "tags": ["a", "b"],
        "model": {"name": "m", "count": 1},
        "color": "red",
    }


def test_final_event_keeps_metrics() -> None:
    result = AgentHarnessResult(
        answer="ok",
        messages=[{"role": "assistant", "content": "ok"}],
        steps=1,
        tool_calls=[],
        metrics={"llm_calls": 1},
    )
    payload = event_to_dict(FinalStructuredEvent("final", 1, result))
    assert payload["type"] == "final"
    assert payload["data"]["answer"] == "ok"
    assert payload["data"]["metrics"] == {"llm_calls": 1}


def test_message_start_event_with_pydantic_message() -> None:
    message = AssistantMessage(
        content=[TextContent(text="hi"), ToolCallContent(id="t1", name="grep")]
    )
    payload = event_to_dict(MessageStartStructuredEvent("message_start", 1, message))
    blocks = payload["data"]["content"]
    assert blocks[0] == {"type": "text", "text": "hi"}
    assert blocks[1]["type"] == "tool_call"
    assert blocks[1]["name"] == "grep"


class _FakeAgent:
    def __init__(self) -> None:
        self.user_approval_callback = None


class _FakeStore:
    session_id = "s1"


class _FakeApp:
    def __init__(self) -> None:
        self.agent = _FakeAgent()
        self.session_store = _FakeStore()
        self._model_info = {"model": "m"}
        self._model_profiles: dict[str, object] = {}

    def get_model_info(self) -> dict[str, str]:
        return dict(self._model_info)

    def mcp_status(self) -> tuple:
        return ()


async def test_hub_rejects_submit_while_running() -> None:
    outgoing: list[dict] = []
    hub = WebRunHub(_FakeApp())

    def _sink(payload: dict) -> None:
        outgoing.append(payload)

    hub.attach(_sink)

    hub._run_task = asyncio.create_task(asyncio.sleep(60))
    try:
        hub.submit("hi", None)
        assert outgoing and outgoing[-1]["type"] == "run_error"
    finally:
        hub._run_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await hub._run_task


def test_model_payload_uses_current_model_effort_capabilities() -> None:
    from cade.server.api import _model_payload

    app = _FakeApp()
    app._model_profiles = {"main": SimpleNamespace(transport="openai_codex")}
    app._model_info = {
        "model": "gpt-5.6-luna",
        "transport": "openai_codex",
        "reasoning_effort": "low",
    }

    with patch(
        "cade.server.api._discover_models",
        return_value=["gpt-5.6-luna"],
    ):
        payload = _model_payload(app)

    assert payload["effort_options"] == [
        "none",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    ]
