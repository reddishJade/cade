"""验证活跃消息和事实重建生成相同的规范化请求。

来源：真实 Codex 会话 20261019-061146 的请求 3 至 6。空文本加工具调用
在活跃路径产生 content: null，重放却省略该字段。运行本模块，逐字节比较
真实组装器的请求；另覆盖空白文本、空回复和不透明回放数据，避免误删。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cade.agent._codec import convert_to_llm
from cade.agent.config import AgentContext
from cade.agent.events import MessageEndEvent
from cade.agent.messages import (
    AgentMessage,
    AssistantMessage,
    ToolResultMessage,
    UserMessage,
)
from cade.agent.request import DefaultRequestAssembler
from cade.agent.types import TextContent, ToolCallContent
from cade.ai.events import ProviderReplayPayload
from cade.harness.agent_runtime.events import (
    ToolResultBlock,
    ToolResultStructuredEvent,
    _StreamTranslationState,
    translate_event,
)
from cade.harness.session.inbox import InboxLane, SessionInbox
from cade.harness.session.recorder import SessionRecorder
from cade.harness.session.store import SessionStore
from cade.harness.session.surface import project_session_surface


@pytest.mark.parametrize("text", ["", " ", "Visible response"])
@pytest.mark.parametrize("has_tool", [False, True])
@pytest.mark.parametrize("has_replay", [False, True])
def test_live_and_replayed_request_bytes_are_identical(
    tmp_path: Path, text: str, has_tool: bool, has_replay: bool
) -> None:
    store = SessionStore(tmp_path / "sessions")
    inbox = SessionInbox(store)
    recorder = SessionRecorder(store)
    inbox.insert(UserMessage(content="Task"), InboxLane.NEXT_TURN, wake=True)
    history: list[AgentMessage] = list(inbox.claim_initial("first-run"))
    assistant = AssistantMessage(
        content=[TextContent(text=text)],
        replay_payload=(
            ProviderReplayPayload(
                protocol="openai_responses",
                transport="openai_codex",
                model="gpt-5.6-luna",
                output_items=(
                    {
                        "type": "reasoning",
                        "id": "reasoning-1",
                        "summary": [],
                        "encrypted_content": "opaque-data",
                    },
                ),
                reasoning_tokens=4,
                output_tokens=8,
            )
            if has_replay
            else None
        ),
    )
    if has_tool:
        assistant.content.append(
            ToolCallContent(id="call-1", name="read", arguments={"path": "example.txt"})
        )
    original = assistant.model_dump_json()
    history.append(assistant)
    event = translate_event(
        MessageEndEvent(message=assistant), _StreamTranslationState()
    )
    assert event is not None and not isinstance(event, list)
    recorder.record_event(event)
    if has_tool:
        history.append(
            ToolResultMessage(tool_call_id="call-1", tool_name="read", content="Result")
        )
        recorder.record_event(
            ToolResultStructuredEvent(
                "tool_result", 1, ToolResultBlock("call-1", "Result")
            )
        )
    inbox.insert(UserMessage(content="Continue"), InboxLane.NEXT_TURN, wake=True)
    history.extend(inbox.claim_initial("next-run"))
    replay = project_session_surface(
        store.build_branch(), artifacts_dir=store.artifacts_dir
    )
    assembler = DefaultRequestAssembler()
    live = assembler.assemble(
        AgentContext(messages=history), current_step=1, options=None
    )
    restored = assembler.assemble(
        AgentContext(messages=list(replay.messages)), current_step=1, options=None
    )
    assert json.dumps(
        live.wire_messages, sort_keys=True, separators=(",", ":")
    ) == json.dumps(restored.wire_messages, sort_keys=True, separators=(",", ":"))
    assert assistant.model_dump_json() == original
    wire = convert_to_llm([assistant])[0]
    if text:
        assert wire["content"] == text
    else:
        assert "content" not in wire
    if has_replay:
        assert wire["_cade_replay_payload"]["output_items"] == [
            dict(item) for item in assistant.replay_payload.output_items
        ]
    if not text and not has_tool and not has_replay:
        assert [message["role"] for message in live.wire_messages] == ["user", "user"]
