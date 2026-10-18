"""中断的工具调用在恢复后结果不确定，需要提示。"""

from __future__ import annotations

from pathlib import Path

from cade.ai.events import ToolCall
from cade.harness.agent_runtime.events import (
    ToolResultBlock,
    ToolResultStructuredEvent,
    ToolUseStructuredEvent,
)
from cade.harness.session.recorder import SessionRecorder
from cade.harness.session.replay import replay_session, unresolved_tool_attempts
from cade.harness.session.store import SessionStore


class _RestoredAgent:
    session_id = ""
    history_session_id = ""
    notice = ""

    def set_history_session_id(self, value: str) -> None:
        self.history_session_id = value

    def load_history(self, messages) -> None:
        self.messages = messages

    def restore_run_state_metadata(self, payload) -> None:
        pass

    def restore_goal_state(self, payload) -> None:
        pass

    def set_resumed_notice(self, notice: str) -> None:
        self.notice = notice


def test_unresolved_attempt_warns_without_fabricated_tool_result(
    tmp_path: Path,
) -> None:
    store = SessionStore(tmp_path / "sessions", project_root=tmp_path)
    recorder = SessionRecorder(store)
    recorder.record_event(
        ToolUseStructuredEvent(
            "tool_use",
            1,
            ToolCall(id="call-1", name="bash", input={"command": "touch file"}),
        )
    )
    assert unresolved_tool_attempts(store.build_branch()) == ("call-1",)
    agent = _RestoredAgent()
    replay_session(agent, store)
    assert "side effects are UNKNOWN" in agent.notice
    assert "before repeating" in agent.notice


def test_recorded_result_closes_unknown_attempt(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions", project_root=tmp_path)
    recorder = SessionRecorder(store)
    recorder.record_event(
        ToolUseStructuredEvent(
            "tool_use", 1, ToolCall(id="call-2", name="bash", input={"command": "pwd"})
        )
    )
    recorder.record_event(
        ToolResultStructuredEvent(
            "tool_result", 1, ToolResultBlock("call-2", "success")
        )
    )
    assert unresolved_tool_attempts(store.build_branch()) == ()
    agent = _RestoredAgent()
    replay_session(agent, store)
    assert "side effects are UNKNOWN" not in agent.notice
