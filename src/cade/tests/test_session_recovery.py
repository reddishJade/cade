"""使用真实会话文件重现恢复边界，并保留可复查的投影产物。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cade.agent.messages import AssistantMessage, UserMessage
from cade.agent.types import TextContent
from cade.harness.agent_runtime.events import (
    AssistantStructuredEvent,
    AssistantTextBlock,
)
from cade.harness.session import InboxLane, SessionInbox, SessionStore
from cade.harness.session.recorder import SessionRecorder
from cade.harness.session.surface import project_session_surface


def _new_session(tmp_path: Path) -> SessionRecorder:
    recorder = SessionRecorder(
        SessionStore(tmp_path / "sessions", project_root=tmp_path)
    )
    recorder.store.ensure_metadata("recovery regression")
    inbox = SessionInbox(recorder.store)
    inbox.insert(
        UserMessage(content="continue the task"), InboxLane.NEXT_TURN, wake=True
    )
    inbox.claim_initial("recovery-run")
    return recorder


def _report(recorder: SessionRecorder, path: Path) -> list[str]:
    reopened = SessionStore(recorder.store.sessions_dir, project_root=path.parent)
    reopened.resume(recorder.store.current_path)
    surface = project_session_surface(reopened.build_branch())
    path.write_text(
        json.dumps(
            {
                "session": str(reopened.current_path),
                "messages": [
                    message.model_dump(mode="json") for message in surface.messages
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return [
        block.text
        for message in surface.messages
        if isinstance(message, AssistantMessage)
        for block in message.content
        if isinstance(block, TextContent)
    ]


def test_recover_text_event_before_final_record(tmp_path: Path) -> None:
    recorder = _new_session(tmp_path)
    recorder.record_event(
        AssistantStructuredEvent(
            "assistant",
            1,
            (AssistantTextBlock("  evidence\n"), AssistantTextBlock("next action  ")),
        )
    )
    assert _report(recorder, tmp_path / "projection.json") == [
        "  evidence\n",
        "next action  ",
    ]


@pytest.mark.parametrize("final", ["answer", "answer extra verification"])
def test_final_record_does_not_duplicate_text_events(
    tmp_path: Path, final: str
) -> None:
    recorder = _new_session(tmp_path)
    recorder.record_event(
        AssistantStructuredEvent("assistant", 1, (AssistantTextBlock("investigating"),))
    )
    recorder.record_event(
        AssistantStructuredEvent("assistant", 2, (AssistantTextBlock("answer"),))
    )
    recorder.record_assistant(final)
    expected = ["investigating", "answer"]
    if final != "answer":
        expected.append("extra verification")
    assert _report(recorder, tmp_path / "projection.json") == expected


def test_identical_text_in_separate_responses_is_preserved(tmp_path: Path) -> None:
    recorder = _new_session(tmp_path)
    for step in (1, 2):
        recorder.record_event(
            AssistantStructuredEvent("assistant", step, (AssistantTextBlock("same"),))
        )
    recorder.record_assistant("same")
    assert _report(recorder, tmp_path / "projection.json") == ["same", "same"]


def test_fork_rebases_window_sources_and_keeps_generations(tmp_path: Path) -> None:
    recorder = _new_session(tmp_path)
    recorder.record_assistant("first answer")
    recorder.record_context_window_reset(
        window_id="window-1",
        messages_before=2,
        messages_after=1,
        replacement=[UserMessage(content="first task")],
    )
    inbox = SessionInbox(recorder.store)
    inbox.insert(UserMessage(content="second task"), InboxLane.NEXT_TURN, wake=True)
    inbox.claim_initial("second-run")
    anchor = recorder.store.get_forkable_user_messages()[-1].id
    recorder.record_context_window_reset(
        window_id="window-2",
        messages_before=2,
        messages_after=1,
        replacement=[UserMessage(content="second task")],
    )
    original_bytes = recorder.store.current_path.read_bytes()
    fork = recorder.store.fork_from_entry(anchor)
    assert recorder.store.current_path.read_bytes() == original_bytes
    assert project_session_surface(fork.build_branch()).messages == (
        UserMessage(content="second task"),
    )
    fork_recorder = SessionRecorder(fork)
    fork_recorder.record_context_window_reset(
        window_id="window-3",
        messages_before=1,
        messages_after=1,
        replacement=[UserMessage(content="next action")],
    )
    assert project_session_surface(fork.build_branch()).generation == 3
    _report(fork_recorder, tmp_path / "fork-projection.json")
    branch = fork.build_branch()
    for index, entry in enumerate(branch):
        if (
            isinstance(entry.content, dict)
            and entry.content.get("type") == "context_window_reset"
        ):
            assert entry.content["data"]["source_entry_ids"] == [
                record.id for record in branch[:index]
            ]


def test_claimed_input_waits_for_complete_tool_batch(tmp_path: Path) -> None:
    from cade.agent.messages import ToolResultMessage
    from cade.harness.agent_runtime.events import (
        AssistantToolUseBlock,
        ToolResultBlock,
        ToolResultStructuredEvent,
    )
    from cade.harness.session.surface import validate_tool_pairing

    recorder = _new_session(tmp_path)
    recorder.record_event(
        AssistantStructuredEvent(
            "assistant",
            1,
            (
                AssistantToolUseBlock("read-a", "read_file", {"path": "a"}),
                AssistantToolUseBlock("read-b", "read_file", {"path": "b"}),
            ),
        )
    )
    recorder.record_event(
        ToolResultStructuredEvent("tool_result", 1, ToolResultBlock("read-a", "a"))
    )
    inbox = SessionInbox(recorder.store)
    inbox.insert(
        UserMessage(content="<reminder>save progress</reminder>"),
        InboxLane.NEXT_STEP,
        source="runtime",
        wake=False,
    )
    inbox.claim_next_step("recovery-run")
    recorder.record_event(
        ToolResultStructuredEvent("tool_result", 1, ToolResultBlock("read-b", "b"))
    )
    _report(recorder, tmp_path / "tool-batch-projection.json")
    messages = list(project_session_surface(recorder.store.build_branch()).messages)
    assert [type(message) for message in messages] == [
        UserMessage,
        AssistantMessage,
        ToolResultMessage,
        ToolResultMessage,
        UserMessage,
    ]
    validate_tool_pairing(messages)


def test_replacement_rejects_input_inside_tool_batch() -> None:
    from cade.agent.messages import ToolResultMessage
    from cade.agent.types import ToolCallContent
    from cade.harness.session.surface import (
        InvalidSessionSurfaceError,
        encode_surface_messages,
    )

    with pytest.raises(InvalidSessionSurfaceError, match="immediately follow"):
        encode_surface_messages(
            [
                AssistantMessage(
                    content=[
                        ToolCallContent(id="read-a", name="read_file", arguments={})
                    ]
                ),
                UserMessage(content="interrupting input"),
                ToolResultMessage(tool_call_id="read-a", content="a"),
            ]
        )
