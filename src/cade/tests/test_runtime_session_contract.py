"""验证运行时独立提交、失败释放和跨进程运行互斥。

覆盖缺口：旧消费方负责记录 final，运行时没有持久会话锁。运行本模块，
使用真实 Harness、日志文件及子进程；从磁盘事件和 active run 检查结果。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import sys
from argparse import Namespace
from collections.abc import AsyncIterator
from dataclasses import asdict
from pathlib import Path

import pytest

from cade.agent.context import InstructionCollector
from cade.agent.messages import AgentMessage, ToolResultMessage
from cade.agent.request import DefaultRequestAssembler, RequestAssembly
from cade.agent.types import ToolSpec
from cade.ai.events import FinalMessage, Message, ProviderEvent
from cade.ai.providers.base import Provider
from cade.ai.types import StreamOptions, ToolDefinition
from cade.ai.usage import UsageTotals
from cade.coding_agent.app import CadeApp
from cade.coding_agent.cli.session_control import SessionRunControl, session_run_status
from cade.coding_agent.execution_modes import build_default_mode_rulesets
from cade.coding_agent.harness import CodingAgentHarness
from cade.coding_agent.modes.exec_mode import _consume_exec_run, _ExecEmitter
from cade.coding_agent.runtime import CodingAgentRuntimeConfig
from cade.harness.agent_runtime.composition import AgentComposition
from cade.harness.agent_runtime.config import (
    AgentRuntimeConfig,
    GateConfig,
    GateRuntimeConfig,
)
from cade.harness.agent_runtime.events import AgentHarnessEvent
from cade.harness.agent_runtime.harness import AgentHarness
from cade.harness.agent_runtime.request_audit import build_request_audit
from cade.harness.config import AgentConfig, CadeRuntimeConfig
from cade.harness.observability import EventCorrelation, HookRecord, SignalHookManager
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
async def test_run_identity_is_persisted_before_claim_and_model_work(
    tmp_path: Path,
) -> None:
    """运行本模块：开始事件必须先落盘，提前关闭不能丢掉待消费输入。"""
    session = RuntimeSession(SessionRecorder(SessionStore(tmp_path / "sessions")))
    provider = TextProvider()
    harness = make_harness(session, provider)
    stream = harness.arun_stream("Task")
    started = await anext(stream)
    assert started.type == "run_started"
    handle = harness.active_run()
    assert handle is not None and handle.run_id == started.data.run_id
    assert started.correlation.run_id == handle.run_id
    assert started.data.session_id == session.recorder.store.session_id
    assert started.data.run_id != started.data.session_id
    records = session.recorder.store.build_branch()
    assert records[-1].content["type"] == "run_started"
    assert not provider.requests
    assert session.inbox.pending_count() == 1
    assert not harness.interrupt(run_id="stale-run")
    await stream.aclose()
    assert harness.active_run() is None
    assert session.inbox.pending_count() == 1
    events = [event async for event in harness.arun_stream(None)]
    run_id = events[0].data.run_id
    assert run_id != started.data.run_id
    assert all(event.correlation.run_id == run_id for event in events)
    records = session.recorder.store.build_branch()
    claims = [
        entry.content
        for entry in records
        if isinstance(entry.content, dict)
        and entry.content.get("type") == "inbox/claimed"
    ]
    assert len(claims) == 1 and claims[0]["data"]["run_id"] == run_id
    requests = [
        entry.content
        for entry in records
        if isinstance(entry.content, dict)
        and entry.content.get("type") == "provider_request"
    ]
    assert requests[0]["correlation"]["run_id"] == run_id


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
    assert (await anext(stream)).type == "run_started"
    await anext(stream)
    await asyncio.wait_for(started.wait(), timeout=3)
    await stream.aclose()
    assert stopped.is_set()
    assert harness.active_run() is None
    with session.run_lock():
        pass


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_type", ["run_started", "provider_request", "final"])
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
    assert len(provider.requests) == (0 if failure_type != "final" else 1)
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


@pytest.mark.asyncio
async def test_request_snapshot_is_durable_reproducible_and_isolated(
    tmp_path: Path,
) -> None:
    session = RuntimeSession(SessionRecorder(SessionStore(tmp_path / "sessions")))
    hooks = SignalHookManager()

    def mutate_observation(record: HookRecord) -> None:
        assert record.metadata is not None
        observed = record.metadata["messages"]
        assert isinstance(observed, list)
        observed[0]["content"] = "Observer mutation"

    hooks.register("before_provider_request", mutate_observation)

    class AuditProvider(TextProvider):
        async def stream(
            self,
            messages: list[Message],
            tools: list[ToolDefinition],
            options: StreamOptions | None = None,
            **kwargs: object,
        ) -> AsyncIterator[ProviderEvent]:
            audits = [
                entry.content
                for entry in session.recorder.store.build_branch()
                if isinstance(entry.content, dict)
                and entry.content.get("type") == "provider_request"
            ]
            data = audits[-1]["data"]
            assert data["messages"] == messages
            assert data["tools"] == [asdict(tool) for tool in tools]
            snapshot = {
                key: data[key]
                for key in (
                    "messages",
                    "tools",
                    "provider",
                    "options",
                    "composition_id",
                )
            }
            encoded = json.dumps(
                snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()
            assert len(encoded) == data["request_bytes"]
            assert (
                hashlib.blake2b(encoded, digest_size=32).hexdigest()
                == data["request_digest"]
            )
            async for event in super().stream(messages, tools, options, **kwargs):
                yield event

    provider = AuditProvider()
    base = make_harness(session, provider)
    harness = AgentHarness(
        base.composition,
        AgentRuntimeConfig(session=session, gate=GateRuntimeConfig(hook_manager=hooks)),
    )
    await harness.arun("Original input")
    assert provider.requests[0][0]["content"] == "Original input"
    await harness.arun("Second task")
    await make_harness(session, provider).arun("Third task")
    requests = [
        entry.content
        for entry in session.recorder.store.build_branch()
        if isinstance(entry.content, dict)
        and entry.content.get("type") == "provider_request"
    ]
    request_ids = [fact["correlation"]["request_id"] for fact in requests]
    turn_ids = [fact["correlation"]["turn_id"] for fact in requests]
    assert len(set(request_ids)) == len(set(turn_ids)) == 3
    assert all(
        fact["correlation"]["session_id"] == session.recorder.store.session_id
        for fact in requests
    )


def test_request_snapshot_preserves_builtins_but_excludes_transport_credentials() -> (
    None
):
    tool = ToolDefinition(
        name="search",
        description="Search",
        parameters={},
        builtin={"type": "web_search"},
    )
    record = build_request_audit(
        RequestAssembly(
            messages=(),
            wire_messages=(),
            tools=(tool,),
            context_trace=(),
            current_step=1,
            options=StreamOptions(
                api_key="private-key",
                headers={"Authorization": "private-header"},
                max_tokens=128,
                signal=object(),
                on_payload=object(),
            ),
        ),
        TextProvider(),
        "composition",
        "prompt",
        EventCorrelation(),
    )
    assert record.metadata is not None
    assert record.metadata["tools"] == [asdict(tool)]
    assert "private-key" not in str(record.metadata)
    assert "private-header" not in str(record.metadata)
    options = record.metadata["options"]
    assert isinstance(options, dict) and options["max_tokens"] == 128
    assert not {"api_key", "headers", "signal", "on_payload"}.intersection(options)


def test_instruction_sources_do_not_depend_on_request_assembler_internals(
    tmp_path: Path,
) -> None:
    instructions = tmp_path / "AGENTS.md"
    instructions.write_text("Project instructions", encoding="utf-8")
    configured = tmp_path / "RULES.md"
    configured.write_text("Configured instructions", encoding="utf-8")
    sources = InstructionCollector(
        sources=(
            {"type": "inline", "content": "Inline instructions"},
            {"type": "file", "path": "RULES.md"},
        ),
        project_root=tmp_path,
    )
    session = RuntimeSession(SessionRecorder(SessionStore(tmp_path / "sessions")))
    base = make_harness(session, TextProvider())
    agent = CodingAgentHarness(
        base.composition, CodingAgentRuntimeConfig(session=session)
    )
    app = CadeApp(agent=agent, _instruction_sources=sources)
    assert app.instruction_paths() == (configured, instructions)
    instructions.unlink()
    assert app.instruction_paths() == (configured,)
    assert session.recorder.store.build_branch() == []


@pytest.mark.parametrize(
    "operation", ["clear", "resume", "rewind", "jump", "fork", "clone", "assign"]
)
def test_active_run_blocks_branch_and_session_transitions(
    tmp_path: Path, operation: str
) -> None:
    store = SessionStore(tmp_path / "sessions")
    entry = store.append("assistant", "Original branch")
    other = SessionStore(tmp_path / "sessions")
    other.resume(store.current_path)
    target = SessionStore(tmp_path / "sessions")
    target.append("assistant", "Other session")
    original_path = other.current_path
    operations = {
        "clear": other.clear,
        "resume": lambda: other.resume(target.current_path),
        "rewind": other.rewind_turns,
        "jump": lambda: other.jump_to_entry(entry),
        "fork": lambda: other.fork_from_entry(entry),
        "clone": other.fork_into,
        "assign": lambda: setattr(other, "current_path", target.current_path),
    }
    with store.run_lock():
        with pytest.raises(RuntimeError, match="already has an active run"):
            operations[operation]()
        assert other.current_path == original_path
        assert [item.id for item in store.build_branch()] == [entry]
        with pytest.raises(RuntimeError, match="already has an active run"):
            target.resume(store.current_path)
    assert other.jump_to_entry(entry)


def test_exec_uses_runtime_identity_for_consecutive_runs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """exec 输出必须引用实际 run；两轮同会话不能复用 session ID。"""
    session = RuntimeSession(SessionRecorder(SessionStore(tmp_path / "sessions")))
    base = make_harness(session, TextProvider())
    agent = CodingAgentHarness(
        base.composition, CodingAgentRuntimeConfig(session=session)
    )
    app = CadeApp(agent=agent, session_recorder=session.recorder)
    args = Namespace(
        project_root=tmp_path,
        model=None,
        transport=None,
        output_last_message=None,
        timeout=None,
        approval="auto-review",
    )
    for question in ("First", "Second"):
        assert (
            _consume_exec_run(
                app, question, CadeRuntimeConfig(), args, _ExecEmitter("jsonl")
            )
            == 0
        )
    output = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    starts = [event for event in output if event["type"] == "run.started"]
    ends = [event for event in output if event["type"] == "run.completed"]
    assert len(starts) == len(ends) == 2
    assert starts[0]["session_id"] == starts[1]["session_id"]
    assert starts[0]["run_id"] != starts[1]["run_id"]
    for start, end in zip(starts, ends, strict=True):
        assert start["run_id"] == end["run_id"] != start["session_id"]
    persisted = [
        entry.content
        for entry in session.recorder.store.build_branch()
        if isinstance(entry.content, dict)
        and entry.content.get("type") == "run_started"
    ]
    assert [event["data"]["run_id"] for event in persisted] == [
        event["run_id"] for event in starts
    ]
    assert session_run_status(
        session.recorder.store.sessions_dir, session.recorder.store.session_id
    ) == {"active": False}


def test_old_host_cleanup_preserves_new_run_control(tmp_path: Path) -> None:
    session = RuntimeSession(SessionRecorder(SessionStore(tmp_path / "sessions")))
    base = make_harness(session, TextProvider())
    app = CadeApp(
        agent=CodingAgentHarness(
            base.composition, CodingAgentRuntimeConfig(session=session)
        )
    )
    store = session.recorder.store
    old = SessionRunControl(store.sessions_dir, store.session_id, app, "old-run")
    new = SessionRunControl(store.sessions_dir, store.session_id, app, "new-run")
    try:
        old.start()
        new.start()
        old.stop()
        assert (
            session_run_status(store.sessions_dir, store.session_id)["run_id"]
            == "new-run"
        )
    finally:
        old.stop()
        new.stop()
    assert session_run_status(store.sessions_dir, store.session_id) == {"active": False}


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
try:
    store.resume(sys.argv[2])
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
