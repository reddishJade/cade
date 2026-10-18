"""终态回答只存为 final 事实，展示与摘要从该事件派生。"""

from __future__ import annotations

from cade.coding_agent.modes.tui.state import _TuiState
from cade.harness.agent_runtime.events import (
    AssistantStructuredEvent,
    AssistantTextBlock,
    FinalStructuredEvent,
)
from cade.harness.agent_runtime.result import AgentHarnessResult
from cade.harness.session.recorder import SessionRecorder
from cade.harness.session.store import SessionStore
from cade.harness.session.surface import project_session_surface


def test_final_answer_uses_one_durable_event_and_restores_ui(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions", project_root=tmp_path)
    recorder = SessionRecorder(store)
    recorder.record_event(
        AssistantStructuredEvent("assistant", 1, (AssistantTextBlock("Done"),))
    )
    final = FinalStructuredEvent(
        "final",
        1,
        AgentHarnessResult(answer="Done", messages=[], steps=1, tool_calls=[]),
    )
    recorder.record_event(final)
    entries = store.build_branch()
    assert [e.type for e in entries] == ["event", "event"]
    assert [e.content["type"] for e in entries] == ["assistant", "final"]
    assert len(project_session_surface(entries).messages) == 1
    state = _TuiState()
    state.restore_history(entries)
    assert [x.content() for x in state.log if x.role == "cade"] == ["Done"]


def test_legacy_final_and_assistant_copy_render_once(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions", project_root=tmp_path)
    store.append("event", {"type": "final", "data": {"answer": "Old answer"}})
    store.append("assistant", "Old answer")
    state = _TuiState()
    state.restore_history(store.build_branch())
    assert [x.content() for x in state.log if x.role == "cade"] == ["Old answer"]


def test_partial_assistant_entry_remains_supported(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions", project_root=tmp_path)
    recorder = SessionRecorder(store)
    recorder.record_assistant("Interrupted partial")
    entries = store.build_branch()
    assert len(entries) == 1
    assert entries[0].type == "assistant"
    state = _TuiState()
    state.restore_history(entries)
    assert [x.content() for x in state.log if x.role == "cade"] == [
        "Interrupted partial"
    ]
