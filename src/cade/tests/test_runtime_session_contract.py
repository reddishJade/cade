"""验证运行时独立提交、失败释放和跨进程运行互斥。

覆盖缺口：旧消费方负责记录 final，运行时没有持久会话锁。运行本模块，
使用真实 Harness、日志文件及子进程；从磁盘事件和 active run 检查结果。
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from cade.agent.messages import AgentMessage, ToolResultMessage
from cade.agent.request import DefaultRequestAssembler
from cade.agent.types import ToolSpec
from cade.ai.events import FinalMessage, Message, ProviderEvent
from cade.ai.providers.base import Provider
from cade.ai.types import StreamOptions, ToolDefinition
from cade.ai.usage import UsageTotals
from cade.coding_agent.execution_modes import build_default_mode_rulesets
from cade.coding_agent.harness import CodingAgentHarness
from cade.coding_agent.runtime import CodingAgentRuntimeConfig
from cade.harness.agent_runtime.composition import AgentComposition
from cade.harness.agent_runtime.config import AgentRuntimeConfig, GateConfig
from cade.harness.agent_runtime.events import AgentHarnessEvent
from cade.harness.agent_runtime.harness import AgentHarness
from cade.harness.config import AgentConfig
from cade.harness.session.recorder import SessionRecorder
from cade.harness.session.replay import unresolved_tool_attempts
from cade.harness.session.runtime import RuntimeSession
from cade.harness.session.store import SessionStore
from cade.harness.session.types import JsonValue
from cade.harness.skills.registry import SkillRegistry
from cade.harness.skills.tools import build_load_skill_tool


class TextProvider(Provider):
    def __init__(self) -> None:
        super().__init__()
        self.requests: list[list[Message]] = []
        self.usage_totals = UsageTotals()
        self.cache_hit_rate: float | None = None

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolDefinition],
        options: StreamOptions | None = None,
        **kwargs: object,
    ) -> AsyncIterator[ProviderEvent]:
        self.requests.append(messages)
        yield FinalMessage(content="Done", stop_reason="end_turn")


def make_harness(
    session: RuntimeSession, provider: TextProvider, tools: tuple[ToolSpec, ...] = ()
) -> AgentHarness:
    return AgentHarness(
        AgentComposition.create(
            primary_provider=provider,
            registry=tools,
            config=AgentConfig(),
            gate=GateConfig(default_mode_rulesets=build_default_mode_rulesets()),
            request_assembler=DefaultRequestAssembler(),
            runtime_context_provider=None,
        ),
        AgentRuntimeConfig(session=session),
    )


@pytest.mark.asyncio
async def test_final_is_durable_before_delivery_and_close_releases_run(
    tmp_path: Path,
) -> None:
    session = RuntimeSession(SessionRecorder(SessionStore(tmp_path / "sessions")))
    harness = make_harness(session, TextProvider())
    stream = harness.arun_stream("Task")
    try:
        async for event in stream:
            if event.type == "final":
                records = session.recorder.store.build_branch()
                content = records[-1].content
                assert isinstance(content, dict) and content["type"] == "final"
                assert harness.active_run() is not None
                break
    finally:
        await stream.aclose()
    assert harness.active_run() is None
    await harness.arun("Next task")


@pytest.mark.asyncio
async def test_early_close_stops_provider_before_releasing_run(tmp_path: Path) -> None:
    started = asyncio.Event()
    stopped = asyncio.Event()

    class BlockingProvider(TextProvider):
        async def stream(
            self,
            messages: list[Message],
            tools: list[ToolDefinition],
            options: StreamOptions | None = None,
            **kwargs: object,
        ) -> AsyncIterator[ProviderEvent]:
            started.set()
            try:
                await asyncio.Event().wait()
                yield FinalMessage(content="Unreachable", stop_reason="end_turn")
            finally:
                stopped.set()

    session = RuntimeSession(SessionRecorder(SessionStore(tmp_path / "sessions")))
    harness = make_harness(session, BlockingProvider())
    stream = harness.arun_stream("Task")
    await anext(stream)
    await asyncio.wait_for(started.wait(), timeout=3)
    await stream.aclose()
    assert stopped.is_set()
    assert harness.active_run() is None
    with session.run_lock():
        pass


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_type", ["provider_request", "final"])
async def test_failed_commit_propagates_and_releases_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_type: str
) -> None:
    session = RuntimeSession(SessionRecorder(SessionStore(tmp_path / "sessions")))
    provider = TextProvider()
    harness = make_harness(session, provider)
    original = session.recorder.store.append

    def fail_append(record_type: str, content: JsonValue) -> str:
        if isinstance(content, dict) and content.get("type") == failure_type:
            raise OSError("Injected commit failure")
        return original(record_type, content)

    monkeypatch.setattr(session.recorder.store, "append", fail_append)
    with pytest.raises(OSError, match="Injected commit failure"):
        await harness.arun("Task")
    assert harness.active_run() is None
    assert len(provider.requests) == (0 if failure_type == "provider_request" else 1)
    assert not any(
        isinstance(entry.content, dict) and entry.content.get("type") == "final"
        for entry in session.recorder.store.build_branch()
    )
    monkeypatch.setattr(session.recorder.store, "append", original)
    await harness.arun("Next task")


@pytest.mark.asyncio
async def test_persisted_facts_survive_interrupted_commit_delivery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = RuntimeSession(SessionRecorder(SessionStore(tmp_path / "sessions")))
    provider = TextProvider()
    harness = make_harness(session, provider)
    original = session.recorder.record_event

    def fail_after_commit(event: AgentHarnessEvent) -> None:
        original(event)
        if event.type == "assistant":
            raise RuntimeError("Injected failure after durable response")

    monkeypatch.setattr(session.recorder, "record_event", fail_after_commit)
    with pytest.raises(RuntimeError, match="after durable response"):
        await harness.arun("First task")
    monkeypatch.setattr(session.recorder, "record_event", original)
    await harness.arun("Second task")
    assert provider.requests[-1][:2] == [
        {"role": "user", "content": "First task"},
        {"role": "assistant", "content": "Done"},
    ]
    cold_provider = TextProvider()
    await make_harness(session, cold_provider).arun("Third task")
    assert cold_provider.requests[0][:2] == provider.requests[-1][:2]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["continuation", "goal"])
async def test_internal_inputs_survive_projection(tmp_path: Path, kind: str) -> None:
    class ContinuingProvider(TextProvider):
        async def stream(
            self,
            messages: list[Message],
            tools: list[ToolDefinition],
            options: StreamOptions | None = None,
            **kwargs: object,
        ) -> AsyncIterator[ProviderEvent]:
            self.requests.append(messages)
            yield FinalMessage(
                content="Partial" if len(self.requests) == 1 else "Done",
                stop_reason=(
                    "max_tokens"
                    if kind == "continuation" and len(self.requests) == 1
                    else "end_turn"
                ),
            )

    class VerifyingHarness(AgentHarness):
        def _build_loop_config_extras(self) -> dict[str, object]:
            attempts = 0

            async def verify(messages: list[AgentMessage]) -> str | None:
                nonlocal attempts
                attempts += 1
                return "Check again" if attempts == 1 and kind == "goal" else None

            return {"completion_verifier": verify}

    session = RuntimeSession(SessionRecorder(SessionStore(tmp_path / "sessions")))
    provider = ContinuingProvider()
    base = make_harness(session, provider)
    harness = VerifyingHarness(base.composition, AgentRuntimeConfig(session=session))
    await harness.arun("Task")
    expected = "continue" if kind == "continuation" else "Check again"
    assert len(provider.requests) == 2
    assert any(message.get("content") == expected for message in provider.requests[1])
    restored = session.history_messages()
    assert any(message.content == expected for message in restored)
    assert any(message.content == "Task" for message in restored)
    cold_provider = TextProvider()
    await make_harness(session, cold_provider).arun("Next task")
    assert any(
        message.get("content") == expected for message in cold_provider.requests[0]
    )


def test_explicit_skill_activation_persists_without_host_recording(
    tmp_path: Path,
) -> None:
    skill = tmp_path / "skills" / "review" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\nname: review\ndescription: Review changes\n---\nCheck behavior.",
        encoding="utf-8",
    )
    registry = SkillRegistry()
    registry.discover([(skill.parent.parent, 10)])
    session = RuntimeSession(SessionRecorder(SessionStore(tmp_path / "sessions")))
    base = make_harness(session, TextProvider(), (build_load_skill_tool(registry),))
    harness = CodingAgentHarness(
        base.composition,
        CodingAgentRuntimeConfig(
            session=session, initial_mode="plan", skill_registry=registry
        ),
    )
    result = harness.activate_skill("review")
    assert result.status == "activated"
    assert "Check behavior." in str(session.history_messages())
    entries = session.recorder.store.build_branch()
    assert len(entries) == 2
    assert harness.activate_skill("review").status == "already_active"
    assert len(session.recorder.store.build_branch()) == 2


@pytest.mark.asyncio
async def test_crash_recovery_records_unknown_outcome_before_new_input(
    tmp_path: Path,
) -> None:
    store = SessionStore(tmp_path / "sessions", project_root=tmp_path)
    store.append("assistant", "Initial context")
    effect = tmp_path / "side-effect.txt"
    script = """
import os
import sys
from pathlib import Path
from cade.agent.messages import UserMessage
from cade.ai.events import ToolCall
from cade.harness.agent_runtime.events import ToolUseStructuredEvent
from cade.harness.session.inbox import SessionInbox, InboxLane
from cade.harness.session.recorder import SessionRecorder
from cade.harness.session.store import SessionStore
store = SessionStore(Path(sys.argv[1]), project_root=Path(sys.argv[2]))
store.resume(sys.argv[3])
inbox = SessionInbox(store)
inbox.insert(UserMessage(content='Run mutation'), InboxLane.NEXT_TURN, wake=True)
inbox.claim_initial('crashed-run')
SessionRecorder(store).record_event(ToolUseStructuredEvent(
    'tool_use', 1, ToolCall(id='uncertain-call', name='mutate', input={})))
Path(sys.argv[4]).write_text('Applied once', encoding='utf-8')
os._exit(73)
"""
    result = await asyncio.to_thread(
        subprocess.run,
        [
            sys.executable,
            "-c",
            script,
            str(store.sessions_dir),
            str(tmp_path),
            store.session_id,
            str(effect),
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 73, result.stderr
    assert unresolved_tool_attempts(store.build_branch()) == ("uncertain-call",)
    session = RuntimeSession(SessionRecorder(store))
    provider = TextProvider()
    harness = make_harness(session, provider)
    await harness.arun("Inspect before retrying")
    assert effect.read_text(encoding="utf-8") == "Applied once"
    assert unresolved_tool_attempts(store.build_branch()) == ()
    assert "Execution outcome is UNKNOWN" in str(provider.requests[0])
    recovered = [
        message
        for message in session.history_messages()
        if isinstance(message, ToolResultMessage)
        and message.tool_call_id == "uncertain-call"
    ]
    assert len(recovered) == 1
    assert recovered[0].metadata == {
        "execution_status": "interrupted",
        "outcome": "unknown",
    }
    await harness.arun("Next task")
    facts = [entry.content for entry in store.build_branch()]
    recovery_positions = [
        index
        for index, fact in enumerate(facts)
        if isinstance(fact, dict) and fact.get("type") == "tool_result"
    ]
    assert len(recovery_positions) == 1
    next_claim = next(
        index
        for index, fact in enumerate(facts)
        if isinstance(fact, dict)
        and fact.get("type") == "inbox/claimed"
        and "Inspect before retrying" in str(fact)
    )
    assert recovery_positions[0] < next_claim


def test_session_run_lock_excludes_other_process_and_releases(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions")
    store.append("assistant", "Existing answer")
    session = RuntimeSession(SessionRecorder(store))
    script = """
import sys
from pathlib import Path
from cade.harness.session.store import SessionStore
from cade.harness.session.recorder import SessionRecorder
from cade.harness.session.runtime import RuntimeSession
store = SessionStore(Path(sys.argv[1]))
store.resume(sys.argv[2])
try:
    with RuntimeSession(SessionRecorder(store)).run_lock():
        print('acquired')
except RuntimeError as exc:
    print(str(exc))
"""

    def attempt() -> str:
        result = subprocess.run(
            [sys.executable, "-c", script, str(store.sessions_dir), store.session_id],
            text=True,
            capture_output=True,
            check=True,
            timeout=10,
        )
        return result.stdout.strip()

    with session.run_lock():
        assert attempt() == "session already has an active run"
    assert attempt() == "acquired"
