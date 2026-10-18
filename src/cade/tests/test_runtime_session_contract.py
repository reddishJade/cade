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

from cade.agent.request import DefaultRequestAssembler
from cade.ai.events import FinalMessage, Message, ProviderEvent
from cade.ai.providers.base import Provider
from cade.ai.types import StreamOptions, ToolDefinition
from cade.ai.usage import UsageTotals
from cade.harness.agent_runtime.composition import AgentComposition
from cade.harness.agent_runtime.config import AgentRuntimeConfig, GateConfig
from cade.harness.agent_runtime.harness import AgentHarness
from cade.harness.config import AgentConfig
from cade.harness.session.recorder import SessionRecorder
from cade.harness.session.runtime import RuntimeSession
from cade.harness.session.store import SessionStore
from cade.harness.session.types import JsonValue


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


def make_harness(session: RuntimeSession, provider: TextProvider) -> AgentHarness:
    return AgentHarness(
        AgentComposition.create(
            primary_provider=provider,
            registry=(),
            config=AgentConfig(),
            gate=GateConfig(),
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
