"""上下文窗口边界是带版本的决策，由事件事实重放得出。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from cade.agent._tool_pairing import repair_tool_pairing
from cade.agent.context_policy import ContextPolicy
from cade.agent.events import ContextWindowResetEvent
from cade.agent.messages import ToolResultMessage, UserMessage
from cade.harness.agent_runtime.context_window import ContextWindowRollover
from cade.harness.agent_runtime.events import (
    AssistantStructuredEvent,
    AssistantToolUseBlock,
    ContextWindowResetStructuredEvent,
    ToolResultBlock,
    ToolResultStructuredEvent,
    _StreamTranslationState,
    translate_event,
)
from cade.harness.session import InboxLane, SessionInbox, SessionStore
from cade.harness.session.recorder import SessionRecorder
from cade.harness.session.surface import (
    InvalidSessionSurfaceError,
    project_session_surface,
    validate_tool_pairing,
)
from cade.harness.session.window_projection import encode_rollover_spec


def _session(
    tmp_path: Path, tool_output: str = "A result"
) -> tuple[SessionStore, SessionRecorder]:
    store = SessionStore(tmp_path / "sessions", project_root=tmp_path)
    inbox = SessionInbox(store)
    inbox.insert(
        UserMessage(content="Inspect source"), lane=InboxLane.NEXT_TURN, wake=True
    )
    inbox.claim_initial("run-1")
    recorder = SessionRecorder(store)
    recorder.record_event(
        AssistantStructuredEvent(
            "assistant", 1, (AssistantToolUseBlock("tool-a", "read", {"path": "a.py"}),)
        )
    )
    recorder.record_event(
        ToolResultStructuredEvent(
            "tool_result",
            1,
            ToolResultBlock(
                "tool-a",
                tool_output,
                metadata={"context_lifetime": "durable", "context_key": "source-file"},
            ),
        )
    )
    return store, recorder


def _source(store: SessionStore) -> list:
    return list(
        project_session_surface(
            store.build_branch(), artifacts_dir=store.artifacts_dir
        ).messages
    )


def test_auto_rollover_replays_window_from_facts(tmp_path: Path) -> None:
    store, recorder = _session(tmp_path)
    before = _source(store)
    policy = ContextPolicy(working_set_token_budget=4096)
    window_id = "stable-window"
    expected = repair_tool_pairing(
        ContextWindowRollover().rollover_messages(
            before, policy=policy, window_id=window_id
        )
    )

    event = ContextWindowResetEvent(
        window_id=window_id,
        trigger="token_limit",
        replacement=expected,
        reproject_from_facts=True,
        rollover_policy=policy,
    )
    translated = translate_event(event, _StreamTranslationState())
    assert isinstance(translated, ContextWindowResetStructuredEvent)
    recorder.record_event(translated)
    branch = store.build_branch()
    reset = branch[-1].content
    assert isinstance(reset, dict)
    data = reset["data"]
    assert isinstance(data, dict)
    assert "replacement" not in data
    assert data["rollover_spec"] == encode_rollover_spec(policy)
    assert data["source_entry_ids"] == [entry.id for entry in branch[:-1]]
    assert (
        list(
            project_session_surface(branch, artifacts_dir=store.artifacts_dir).messages
        )
        == expected
    )

    # 边界之前的 fork 重建原始事实，而不是新窗口。
    claimed = next(
        entry
        for entry in branch
        if entry.type == "event"
        and isinstance(entry.content, dict)
        and entry.content.get("type") == "inbox/claimed"
    )
    fork = store.fork_from_entry(claimed.id)
    assert not any(
        e.type == "event"
        and isinstance(e.content, dict)
        and e.content.get("type") == "context_window_reset"
        for e in fork.build_branch()
    )


def test_manual_rollover_preserves_retention_choice_and_multiple_generations(
    tmp_path: Path,
) -> None:
    store, recorder = _session(tmp_path)
    policy = ContextPolicy(working_set_token_budget=2048)
    first = ContextWindowRollover().rollover_messages(
        _source(store), policy=policy, window_id="manual-1"
    )
    recorder.record_context_window_reset(
        window_id="manual-1",
        messages_before=3,
        messages_after=len(first),
        replacement=first,
        reproject_from_facts=True,
        rollover_policy=policy,
        preserve_user_request=True,
    )
    assert list(
        project_session_surface(
            store.build_branch(), artifacts_dir=store.artifacts_dir
        ).messages
    ) == repair_tool_pairing(first)

    second = ContextWindowRollover().rollover_messages(
        _source(store),
        policy=policy,
        preserve_user_request=False,
        window_id="manual-2",
    )
    recorder.record_context_window_reset(
        window_id="manual-2",
        messages_before=len(first),
        messages_after=len(second),
        replacement=second,
        reproject_from_facts=True,
        rollover_policy=policy,
        preserve_user_request=False,
    )
    branch = store.build_branch()
    surface = project_session_surface(branch, artifacts_dir=store.artifacts_dir)
    assert surface.generation == 2
    assert list(surface.messages) == repair_tool_pairing(second)
    validate_tool_pairing(list(surface.messages))
    assert not any(isinstance(m, UserMessage) for m in surface.messages)
    last = branch[-1].content
    assert isinstance(last, dict)
    data = last["data"]
    assert isinstance(data, dict)
    spec = data["rollover_spec"]
    assert isinstance(spec, dict)
    assert spec["preserve_user_request"] is False
    assert "replacement" not in data


def test_large_tool_artifact_is_hydrated_before_rollover(tmp_path: Path) -> None:
    full_text = "full immutable evidence:" + "x" * 40_000
    store, recorder = _session(tmp_path, full_text)
    before = _source(store)
    result = next(m for m in before if isinstance(m, ToolResultMessage))
    assert result.content == full_text
    policy = ContextPolicy(working_set_token_budget=128)
    expected = repair_tool_pairing(
        ContextWindowRollover().rollover_messages(
            before, policy=policy, window_id="artifact-window"
        )
    )
    recorder.record_context_window_reset(
        window_id="artifact-window",
        messages_before=len(before),
        messages_after=len(expected),
        replacement=expected,
        reproject_from_facts=True,
        rollover_policy=policy,
    )
    restored = _source(store)
    assert restored == expected
    assert (
        next(m for m in restored if isinstance(m, ToolResultMessage)).content
        == full_text
    )


def test_legacy_replacement_still_replays_without_recipe(tmp_path: Path) -> None:
    store, recorder = _session(tmp_path)
    recorder.record_context_window_reset(
        window_id="legacy-window",
        messages_before=3,
        messages_after=1,
        replacement=[UserMessage(content="Historical fixed surface")],
    )
    branch = store.build_branch()
    reset = branch[-1].content
    assert isinstance(reset, dict)
    data = reset["data"]
    assert isinstance(data, dict)
    assert "rollover_spec" not in data
    assert "replacement" in data
    assert list(project_session_surface(branch).messages) == [
        UserMessage(content="Historical fixed surface")
    ]


@pytest.mark.parametrize("invalid", ["mixed", "version", "policy", "source"])
def test_invalid_replay_recipe_or_lineage_is_rejected(
    tmp_path: Path, invalid: str
) -> None:
    store, recorder = _session(tmp_path)
    source = _source(store)
    recorder.record_context_window_reset(
        window_id="tamper-check",
        messages_before=len(source),
        messages_after=len(source),
        replacement=source,
        reproject_from_facts=True,
        rollover_policy=ContextPolicy(),
    )
    branch = store.build_branch()
    encoded = deepcopy(branch[-1].content)
    assert isinstance(encoded, dict)
    data = encoded["data"]
    assert isinstance(data, dict)
    spec = data["rollover_spec"]
    assert isinstance(spec, dict)
    if invalid == "mixed":
        data["replacement"] = []
    elif invalid == "version":
        spec["version"] = 99
    elif invalid == "policy":
        policy = spec["policy"]
        assert isinstance(policy, dict)
        policy["physical_window"] = "invalid"
    else:
        data["source_entry_ids"] = []
    damaged = [*branch[:-1], replace(branch[-1], content=encoded)]
    with pytest.raises(InvalidSessionSurfaceError):
        project_session_surface(damaged, artifacts_dir=store.artifacts_dir)
