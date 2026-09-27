"""预算 E2E：真实 SDK/HTTP/工具链，以及可选的 Luna high 实际调用。

复现：CADE_LIVE_CONTEXT_E2E=1 uv run pytest src/cade/tests/test_context_budget_e2e.py
-q --basetemp=/tmp/cade-context-budget-e2e。请求与结果只保存到 pytest 临时目录。
本地 HTTP 用于注入真实服务难以稳定触发的超限响应，不冒充模型验证。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread

import pytest

import cade.agent.context as context_module
from cade.agent._context_window import estimate_tokens
from cade.agent.agent import Agent
from cade.agent.config import AgentLoopConfig, ContextWindowResetReason
from cade.agent.context import (
    ContextCollectorRegistry,
    NotesCollector,
    make_collector_section,
)
from cade.agent.context_manager import ContextManager
from cade.agent.context_policy import ContextPolicy
from cade.agent.events import AgentEvent, ToolExecutionEndEvent
from cade.agent.messages import (
    AgentMessage,
    AssistantMessage,
    SystemMessage,
    ToolResultMessage,
    UserMessage,
)
from cade.agent.request import DefaultRequestAssembler, RequestAssembly, RequestHygiene
from cade.agent.results import AgentLoopResult, TerminationReason
from cade.agent.types import (
    TextContent,
    ToolCallContent,
    ToolInput,
    ToolOutput,
    ToolSpec,
    ToolSpecAdapter,
)
from cade.ai.providers.openai import OpenAIChatProvider
from cade.ai.providers.responses import (
    OpenAICodexResponsesProvider,
    OpenAIResponsesProvider,
)
from cade.ai.types import ProviderConfig
from cade.harness.agent_runtime.context_window import ContextWindowRollover
from cade.harness.agent_runtime.events import ToolResultBlock, ToolResultStructuredEvent
from cade.harness.auth.manager import AuthManager
from cade.harness.session import SessionHistory, SessionStore
from cade.harness.session.history import build_history_tools
from cade.harness.session.recorder import SessionRecorder


def _trace(assembly: RequestAssembly) -> dict[str, object]:
    return {
        "messages": list(assembly.wire_messages),
        "tokens": assembly.estimated_tokens,
        "local_tokens": assembly.local_estimated_tokens,
        "source": assembly.token_estimate_source,
        "budget": assembly.token_budget,
        "evidence_omitted": list(assembly.evidence_omitted),
        "working_omitted": list(assembly.working_omitted),
        "evidence_reclaimed_tokens": assembly.evidence_reclaimed_tokens,
        "rotation_blocked_reason": assembly.rotation_blocked_reason,
        "mandatory_tokens": assembly.mandatory_estimated_tokens,
        "context_snapshot": asdict(assembly.context_snapshot)
        if assembly.context_snapshot
        else None,
        "max_output_tokens": assembly.options.max_tokens if assembly.options else None,
        "policy": (
            {
                "physical_window": assembly.context_policy.physical_window,
                "output_reserve": assembly.context_policy.output_reserve,
                "headroom": assembly.context_policy.headroom,
                "input_budget": assembly.context_policy.input_budget,
                "evidence_budget": assembly.context_policy.evidence_budget,
                "output_limit_supported": assembly.context_policy.output_limit_supported,
            }
            if assembly.context_policy is not None
            else None
        ),
    }


def _save(
    path: Path,
    requests: list[dict[str, object]],
    results: list[AgentLoopResult],
    events: list[AgentEvent],
) -> None:
    path.write_text(
        json.dumps(
            {
                "requests": requests,
                "results": [
                    result.model_dump(mode="json", exclude={"active_provider"})
                    for result in results
                ],
                "events": [event.model_dump(mode="json") for event in events],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


@contextmanager
def _http_provider(
    scenario: str,
) -> Iterator[
    tuple[OpenAIChatProvider | OpenAIResponsesProvider, list[dict[str, object]]]
]:
    """通过真实 HTTP 注入协议故障，并保留实际发送的消息。"""
    requests: list[dict[str, object]] = []
    release_idle = Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass

        def do_POST(self) -> None:
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(payload)
            index = len(requests) - 1
            if scenario == "responses-idle":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.flush()
                release_idle.wait(timeout=15)
                return
            tool_call = (scenario == "tool-overflow" and index == 0) or (
                scenario in {"evidence", "evidence-pressure", "working-pressure"}
                and index < 3
            )
            fail = (
                scenario in {"permanent", "ordinary-413", "disabled", "call-limit"}
                or (scenario == "tool-overflow" and index == 1)
                or (scenario == "recover" and index == 0)
                or (scenario == "lifecycle" and index == 3)
                or (scenario == "responses-code" and index == 0)
            )
            if fail:
                detail = (
                    "Request entity too large"
                    if scenario in {"ordinary-413", "lifecycle"}
                    else "context_length_exceeded: maximum context length exceeded"
                )
                error = (
                    {"code": "context_length_exceeded"}
                    if scenario == "responses-code"
                    else {"message": detail}
                )
                body = json.dumps({"error": error}).encode()
                self.send_response(413 if scenario == "ordinary-413" else 400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            delta: dict[str, object] = {"content": "done"}
            if tool_call:
                delta = {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": (
                                ["evidence-old", "evidence-new", "evidence-durable"][
                                    index
                                ]
                                if scenario
                                in {"evidence", "evidence-pressure", "working-pressure"}
                                else "read-evidence-1"
                            ),
                            "type": "function",
                            "function": {
                                "name": "read_evidence",
                                "arguments": (
                                    json.dumps(
                                        {"context": "WORKING_RAW_ARGUMENT_" * 1200}
                                    )
                                    if scenario == "working-pressure"
                                    else "{}"
                                ),
                            },
                        }
                    ]
                }
            prompt_tokens = 0 if scenario == "lifecycle" and index == 1 else 100
            if scenario in {"evidence-pressure", "working-pressure"}:
                prompt_tokens = estimate_tokens(json.dumps(payload))
            chunk = {
                "id": f"reply-{index}",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "protocol-fixture",
                "choices": [{"index": 0, "delta": delta, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": 10,
                    "total_tokens": prompt_tokens + 10,
                },
            }
            body = f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode()
            if scenario == "responses-code":
                response_events = [
                    {"type": "response.output_text.delta", "delta": "done"},
                    {
                        "type": "response.completed",
                        "response": {
                            "id": f"response-{index}",
                            "output": [],
                            "usage": {"input_tokens": 100, "output_tokens": 10},
                        },
                    },
                ]
                body = "".join(
                    f"data: {json.dumps(event)}\n\n" for event in response_events
                ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        provider_class = (
            OpenAIResponsesProvider
            if scenario in {"responses-code", "responses-idle"}
            else OpenAIChatProvider
        )
        provider = provider_class(
            ProviderConfig(
                api_key="local-test-only",
                model="protocol-fixture",
                base_url=f"http://127.0.0.1:{server.server_port}/v1",
                thinking=False,
            )
        )
        yield provider, requests
    finally:
        release_idle.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize(
    ("scenario", "expected_calls", "expected_resets", "expected_reason"),
    [
        ("recover", 2, 1, TerminationReason.COMPLETED),
        ("responses-code", 2, 1, TerminationReason.COMPLETED),
        ("tool-overflow", 3, 1, TerminationReason.COMPLETED),
        ("permanent", 2, 1, TerminationReason.PROVIDER_ERROR),
        ("ordinary-413", 1, 0, TerminationReason.PROVIDER_ERROR),
        ("disabled", 1, 0, TerminationReason.PROVIDER_ERROR),
        ("call-limit", 1, 1, TerminationReason.LLM_CALL_LIMIT),
    ],
)
async def test_context_overflow_http_e2e(
    tmp_path: Path,
    scenario: str,
    expected_calls: int,
    expected_resets: int,
    expected_reason: TerminationReason,
) -> None:
    evidence = tmp_path / "evidence.txt"
    evidence.write_text("EXACT_EVIDENCE\n" * 200, encoding="utf-8")
    reads: list[str] = []

    def read_evidence(
        params: ToolInput, on_update: Callable[[str], None] | None
    ) -> str:
        del params, on_update
        reads.append(str(evidence))
        return evidence.read_text(encoding="utf-8")

    tool = ToolSpecAdapter(
        ToolSpec(
            "read_evidence",
            "读取工作区证据文件",
            "{}",
            read_evidence,
            schema={"type": "object", "properties": {}},
        )
    )
    manager = ContextManager()
    manager.replace_history([UserMessage(content="old history")])
    events: list[AgentEvent] = []
    assemblies: list[dict[str, object]] = []
    prefix_refreshes: list[str] = []

    def refresh_prefix() -> list[AgentMessage]:
        prefix_refreshes.append("refreshed")
        return [SystemMessage(content="fresh runtime prefix")]

    with _http_provider(scenario) as (provider, http_requests):
        result = await Agent(tools=[tool], model=provider).run(
            [UserMessage(content="Read the evidence and finish.")],
            AgentLoopConfig(
                provider=provider,
                max_llm_calls=1 if scenario == "call-limit" else 5,
                request_token_budget=1,
                context_policy=ContextPolicy(
                    physical_window=5000,
                    output_reserve=512,
                    headroom_tokens=256,
                    automatic_rollover=scenario != "disabled",
                ),
                recover_context_overflow=scenario != "disabled",
                before_provider_request=lambda assembly: assemblies.append(
                    _trace(assembly)
                ),
                rollover_context=ContextWindowRollover().rollover_messages,
                refresh_request_prefix=refresh_prefix,
                retry_backoff_base=0,
            ),
            request_prefix=[SystemMessage(content="old runtime prefix")],
            context_manager=manager,
            emit=events.append,
        )
    _save(tmp_path / "agent-trace.json", assemblies, [result], events)
    (tmp_path / "http-requests.json").write_text(
        json.dumps(http_requests, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    assert result.termination_reason is expected_reason
    assert len(http_requests) == expected_calls
    assert result.metrics is not None
    assert result.metrics.context_window_resets == expected_resets
    assert len(prefix_refreshes) == expected_resets
    if expected_resets and expected_calls > 1:
        last_request = json.dumps(http_requests[-1])
        assert "fresh runtime prefix" in last_request
        assert "old runtime prefix" not in last_request
        assert assemblies[-1]["source"] == "local"
    if scenario == "tool-overflow":
        assert reads == [str(evidence)]
        assert "EXACT_EVIDENCE" in json.dumps(http_requests[1])
        recovered_body = next(
            message["content"]
            for message in http_requests[2]["messages"]
            if message.get("tool_call_id") == "read-evidence-1"
        )
        assert len(recovered_body) < len(evidence.read_text())
        assert "exact output remains in history" in recovered_body
        assert "read-evidence-1" in json.dumps(http_requests[2])


def test_exec_timeout_idle_responses_http_e2e(tmp_path: Path) -> None:
    """真实 CLI 超时应中止未产生任何模型事件的 HTTP 请求。"""
    with _http_provider("responses-idle") as (provider, requests):
        config = tmp_path / "config.json"
        config.write_text(
            json.dumps(
                {
                    "provider": {
                        "model_profiles": {
                            "main": {
                                "transport": "openai_responses",
                                "chat_model": "protocol-fixture",
                                "api_key": "local-test-only",
                                "base_url": provider.config.base_url,
                                "thinking": False,
                            }
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        command = [
            sys.executable,
            "-m",
            "cade.main",
            "exec",
            "--project-root",
            str(tmp_path),
            "--config",
            str(config),
            "--sessions-dir",
            str(tmp_path / "sessions"),
            "--approval",
            "never",
            "--timeout",
            "2s",
            "--max-steps",
            "2",
            "--max-llm-calls",
            "2",
            "Reply OK.",
        ]
        started = time.monotonic()
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=10, check=False
        )
        elapsed = time.monotonic() - started
    (tmp_path / "stdout.jsonl").write_text(result.stdout, encoding="utf-8")
    (tmp_path / "stderr.txt").write_text(result.stderr, encoding="utf-8")
    (tmp_path / "reproduce.json").write_text(
        json.dumps(
            {
                "command": command,
                "elapsed_seconds": elapsed,
                "requests": requests,
                "returncode": result.returncode,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    assert requests
    assert result.returncode == 124, result.stderr
    rows = [json.loads(line) for line in result.stdout.splitlines()]
    completed = next(row for row in rows if row["type"] == "run.completed")
    assert completed["status"] == "timed_out"
    assert completed["termination_reason"] == "cancelled"
    assert elapsed < 8


@pytest.mark.parametrize("pressure", [False, True, "working"])
async def test_evidence_projection_and_history_reopen_http_e2e(
    tmp_path: Path, pressure: bool | str
) -> None:
    """请求省略旧正文，真实文件原文可从重开的 session 完整找回。"""
    texts = [
        "OLD_EXACT_MARKER\n" * 2400,
        "RECENT_EVIDENCE\n" * 12,
        "DURABLE_INSTRUCTIONS\n" * 600,
    ]
    files = [tmp_path / f"evidence-{index}.txt" for index in range(3)]
    for path, text in zip(files, texts, strict=True):
        path.write_text(text, encoding="utf-8")
    reads: list[str] = []

    def read_evidence(_params: ToolInput, _update: Callable[[str], None] | None) -> str:
        index = len(reads)
        reads.append(str(files[index]))
        return ToolOutput(
            files[index].read_text(encoding="utf-8"),
            metadata={"context_lifetime": "durable"}
            if index == 2
            else {"source": "file"},
        )

    recorder = SessionRecorder(
        SessionStore(tmp_path / "sessions", project_root=tmp_path)
    )
    events: list[AgentEvent] = []

    def emit(event: AgentEvent) -> None:
        events.append(event)
        if isinstance(event, ToolExecutionEndEvent) and event.result is not None:
            result = event.result
            assert isinstance(result.content, str)
            recorder.record_event(
                ToolResultStructuredEvent(
                    "tool_result",
                    len(reads),
                    ToolResultBlock(
                        result.tool_call_id,
                        result.content,
                        status="error" if result.is_error else "ok",
                        metadata=result.metadata,
                    ),
                )
            )

    manager = ContextManager()
    assemblies: list[dict[str, object]] = []
    with _http_provider(
        "working-pressure"
        if pressure == "working"
        else "evidence-pressure"
        if pressure
        else "evidence"
    ) as (
        provider,
        http_requests,
    ):
        result = await Agent(
            tools=[
                ToolSpecAdapter(
                    ToolSpec(
                        "read_evidence",
                        "读取工作区证据文件",
                        "{}",
                        read_evidence,
                        schema={"type": "object", "properties": {}},
                    )
                )
            ],
            model=provider,
        ).run(
            [UserMessage(content="Read all three evidence files and finish.")],
            AgentLoopConfig(
                provider=provider,
                max_llm_calls=5,
                context_policy=ContextPolicy(
                    physical_window=16000,
                    output_reserve=512,
                    headroom_tokens=512,
                    evidence_token_budget=None if pressure else 400,
                ),
                request_assembler=DefaultRequestAssembler(
                    hygiene=RequestHygiene(
                        enabled=not pressure, max_tool_result_bytes=60000
                    )
                ),
                before_provider_request=lambda assembly: assemblies.append(
                    _trace(assembly)
                ),
            ),
            context_manager=manager,
            emit=emit,
        )
    _save(tmp_path / "evidence-trace.json", assemblies, [result], events)
    (tmp_path / "http-requests.json").write_text(
        json.dumps(http_requests, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    assert result.termination_reason is TerminationReason.COMPLETED
    assert reads == [str(path) for path in files]
    last_request = http_requests[-1]
    encoded = json.dumps(last_request)
    if pressure != "working":
        assert "tool_call_id=evidence-old" in encoded
    else:
        assert assemblies[-1]["working_omitted"] == ["evidence-old"]
    assert texts[0] not in encoded
    messages = last_request["messages"]
    assert isinstance(messages, list)
    tool_bodies = {
        message["tool_call_id"]: message["content"]
        for message in messages
        if message["role"] == "tool"
    }
    assert tool_bodies["evidence-new"] == texts[1]
    assert tool_bodies["evidence-durable"] == texts[2]
    assert assemblies[-1]["evidence_omitted"] == ["evidence-old"]
    if pressure:
        assert int(assemblies[-1]["evidence_reclaimed_tokens"]) > 0
        assert int(assemblies[-1]["tokens"]) <= int(assemblies[-1]["budget"])
        if pressure != "working":
            first_messages = http_requests[1]["messages"]
            assert (
                next(
                    m["content"]
                    for m in first_messages
                    if m.get("tool_call_id") == "evidence-old"
                )
                == texts[0]
            )
    assert (
        last_request.get("max_completion_tokens", last_request.get("max_tokens")) == 512
    )
    raw = [
        message for message in manager.history if isinstance(message, ToolResultMessage)
    ]
    assert [message.content for message in raw] == texts
    assert raw[0].metadata == {"source": "file"}

    reopened = SessionStore(tmp_path / "sessions", project_root=tmp_path)
    reopened.resume(recorder.store.session_id)
    history = SessionHistory(
        reopened.sessions_dir, artifacts_dir=reopened.artifacts_dir
    )
    history.set_session_id(reopened.session_id)
    matches = history.search("evidence-old")
    assert matches
    recovered = history.read(matches[0].id, max_chars=20000)
    assert recovered is not None
    pages = [recovered.content]
    while recovered.next_offset is not None:
        recovered = history.read(
            matches[0].id, offset=recovered.next_offset, max_chars=20000
        )
        assert recovered is not None
        pages.append(recovered.content)
    recovered_text = "".join(pages)
    assert json.loads(recovered_text)["data"]["content"] == texts[0]
    (tmp_path / "recovered-history.txt").write_text(recovered_text, encoding="utf-8")
    assert list(reopened.artifacts_dir.rglob("*.txt"))


async def test_request_anchor_lifecycle_http_e2e(tmp_path: Path) -> None:
    """真实请求覆盖零用量、失败、最新笔记/前缀、工具/模型变化和投影替换。"""
    manager = ContextManager()
    assemblies: list[dict[str, object]] = []
    results: list[AgentLoopResult] = []
    events: list[AgentEvent] = []
    prefix = [SystemMessage(content="original instructions")]
    note = tmp_path / "NOTE.md"
    note.write_text("old frontier: investigate", encoding="utf-8")
    collectors = ContextCollectorRegistry()
    collectors.register_section(
        make_collector_section("notes", NotesCollector(tmp_path))
    )
    with _http_provider("lifecycle") as (provider, http_requests):
        agent = Agent(tools=[], model=provider)
        config = AgentLoopConfig(
            provider=provider,
            context_policy=ContextPolicy(
                physical_window=5000, output_reserve=512, headroom_tokens=256
            ),
            request_assembler=DefaultRequestAssembler(context_collectors=collectors),
            max_llm_calls=1,
            before_provider_request=lambda assembly: assemblies.append(
                _trace(assembly)
            ),
            max_step_retries=0,
        )

        async def run() -> None:
            results.append(
                await agent.run(
                    [UserMessage(content="continue")],
                    config,
                    request_prefix=prefix,
                    context_manager=manager,
                    emit=events.append,
                )
            )

        await run()
        note.write_text("updated frontier: a new decision", encoding="utf-8")
        await run()  # 服务端报告零输入，不更新有效锚点。
        await run()
        note.write_text("updated frontier: verify the new decision", encoding="utf-8")
        await run()  # 普通 HTTP 400 失败，不更新有效锚点。
        await run()
        prefix = [SystemMessage(content="revised instructions")]
        note.write_text("new frontier: verify", encoding="utf-8")
        await run()
        note.unlink()
        agent.update_tools(
            [
                ToolSpecAdapter(
                    ToolSpec(
                        "read_status",
                        "读取状态",
                        "{}",
                        lambda _params, _update: "ok",
                        schema={"type": "object", "properties": {}},
                    )
                )
            ]
        )
        await run()
        provider.config = replace(provider.config, model="protocol-fixture-v2")
        await run()
        manager.replace_history([UserMessage(content="replacement history")])
        await run()
        manager.complete_rollover([UserMessage(content="fresh history")])
        await run()
    _save(tmp_path / "anchor-lifecycle.json", assemblies, results, events)
    (tmp_path / "http-requests.json").write_text(
        json.dumps(http_requests, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    assert [assembly["source"] for assembly in assemblies] == [
        "local",
        "provider_anchor",
        "provider_anchor",
        "provider_anchor",
        "provider_anchor",
        "local",
        "local",
        "local",
        "local",
        "local",
    ]
    changed_request = json.dumps(http_requests[5])
    assert "new frontier: verify" in changed_request
    assert "old frontier" not in changed_request
    assert "original instructions" not in changed_request
    assert "revised instructions" in changed_request
    assert "frontier:" not in json.dumps(http_requests[6])
    assert int(assemblies[2]["tokens"]) >= 100
    assert results[3].termination_reason is TerminationReason.PROVIDER_ERROR
    assert all(
        result.termination_reason is TerminationReason.COMPLETED
        for index, result in enumerate(results)
        if index != 3
    )


async def test_irreducible_context_does_not_rotate_repeatedly_http_e2e(
    tmp_path: Path,
) -> None:
    """必需前缀超出本地预算时仍可请求 provider，不随工具迭代反复换窗。"""
    requests: list[dict[str, object]] = []
    events: list[AgentEvent] = []
    with _http_provider("evidence") as (provider, http_requests):
        result = await Agent(
            tools=[
                ToolSpecAdapter(
                    ToolSpec(
                        "read_evidence",
                        "读取短证据",
                        "{}",
                        lambda _params, _update: "short evidence",
                        schema={"type": "object", "properties": {}},
                    )
                )
            ],
            model=provider,
        ).run(
            [UserMessage(content="Continue the task through three tool results.")],
            AgentLoopConfig(
                provider=provider,
                context_policy=ContextPolicy(physical_window=2000, output_reserve=512),
                rollover_context=ContextWindowRollover().rollover_messages,
                before_provider_request=lambda assembly: requests.append(
                    _trace(assembly)
                ),
                max_llm_calls=5,
            ),
            request_prefix=[SystemMessage(content="mandatory instructions\n" * 2000)],
            emit=events.append,
        )
    _save(tmp_path / "irreducible-context.json", requests, [result], events)
    (tmp_path / "http-requests.json").write_text(
        json.dumps(http_requests, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    assert result.termination_reason is TerminationReason.COMPLETED
    assert result.metrics is not None and result.metrics.context_window_resets == 0
    assert len(http_requests) == 4
    assert all(
        r["rotation_blocked_reason"] == "mandatory_input_exceeds_budget"
        for r in requests
    )


@pytest.mark.skipif(
    os.environ.get("CADE_LIVE_CONTEXT_E2E") != "1",
    reason="显式启用后调用真实 gpt-6-luna high，默认不消耗模型配额",
)
async def test_luna_high_measured_budget_e2e(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    credential = AuthManager().get_valid_credential("openai-codex")
    if credential is None or not credential.access:
        pytest.fail("真实 E2E 需要已有 Codex 登录凭据")
    provider = OpenAICodexResponsesProvider(
        ProviderConfig(
            api_key=credential.access,
            model="gpt-6-luna",
            base_url="https://chatgpt.com/backend-api",
            thinking=True,
            reasoning_effort="high",
            extra={"account_id": credential.account_id},
        )
    )
    manager = ContextManager()
    agent = Agent(tools=[], model=provider)
    requests: list[dict[str, object]] = []
    results: list[AgentLoopResult] = []
    events: list[AgentEvent] = []
    decisions: list[dict[str, object]] = []
    rollover = ContextWindowRollover()
    original_estimate = context_module.estimate_tokens

    async def run(prompt: str, *, rotate: bool = False) -> AgentLoopResult:
        def decide(
            _messages: list[AgentMessage], tokens: int | None
        ) -> ContextWindowResetReason | None:
            decisions.append({"predicted_tokens": tokens, "threshold": 1000})
            return "token_limit" if (tokens or 0) >= 1000 else None

        result = await agent.run(
            [UserMessage(content=prompt)],
            AgentLoopConfig(
                provider=provider,
                max_llm_calls=2,
                request_token_budget=1000,
                context_policy=ContextPolicy(
                    physical_window=2000, output_reserve=1000, trigger_ratio=1.0
                ),
                before_provider_request=lambda assembly: requests.append(
                    _trace(assembly)
                ),
                request_rollover_decision=decide if rotate else None,
                rollover_context=rollover.rollover_messages if rotate else None,
            ),
            request_prefix=[
                SystemMessage(content="Reply with exactly OK. Do not use tools.")
            ],
            context_manager=manager,
            emit=events.append,
        )
        results.append(result)
        _save(tmp_path / "luna-high-trace.json", requests, results, events)
        (tmp_path / "rollover-decisions.json").write_text(
            json.dumps(decisions, indent=2), encoding="utf-8"
        )
        assert result.termination_reason is TerminationReason.COMPLETED, (
            result.error_detail
        )
        return result

    # 首次请求即使本地高估也应到达 provider；随后不让全量误差压过实测。
    monkeypatch.setattr(
        context_module, "estimate_tokens", lambda text: original_estimate(text) * 500
    )
    await run("Acknowledge.")
    assert requests[-1]["source"] == "local"
    assert int(requests[-1]["local_tokens"]) > 1000
    await run("Acknowledge again.", rotate=True)
    assert requests[-1]["source"] == "provider_anchor"
    assert int(requests[-1]["local_tokens"]) > 1000
    assert results[-1].metrics is not None
    assert results[-1].metrics.context_window_resets == 0

    # 大量新增内容仍需计入，即使整包估算被故障注入压低。
    monkeypatch.setattr(context_module, "estimate_tokens", lambda _text: 1)
    await run("Evidence:\n" + "distinct evidence 0123456789\n" * 450, rotate=True)
    assert results[-1].metrics is not None
    assert results[-1].metrics.context_window_resets == 1
    assert requests[-1]["source"] == "local"

    # 改写已有投影不能复用旧输入用量。
    monkeypatch.setattr(context_module, "estimate_tokens", original_estimate)
    manager.replace_history(
        [UserMessage(content="A revised task replaces the history.")]
    )
    await run("Acknowledge revised task.")
    assert requests[-1]["source"] == "local"


@pytest.mark.skipif(
    os.environ.get("CADE_LIVE_CONTEXT_E2E") != "1",
    reason="显式启用后调用真实 gpt-6-luna high，验证跨窗冷证据召回",
)
async def test_luna_high_recovers_omitted_evidence_after_reopen_e2e(
    tmp_path: Path,
) -> None:
    """新窗口模型只能通过真实 history 工具取回旧 artifact 中被省略的随机值。"""
    credential = AuthManager().get_valid_credential("openai-codex")
    if credential is None or not credential.access:
        pytest.fail("真实 E2E 需要已有 Codex 登录凭据")
    provider = OpenAICodexResponsesProvider(
        ProviderConfig(
            api_key=credential.access,
            model="gpt-6-luna",
            base_url="https://chatgpt.com/backend-api",
            thinking=True,
            reasoning_effort="high",
            extra={"account_id": credential.account_id},
        )
    )
    secret = os.urandom(12).hex()
    body = (
        "unrelated evidence row 0123456789\n" * 150
        + f"RECOVERY_SECRET={secret}\n"
        + "unrelated evidence row 9876543210\n" * 1200
    )
    recorder = SessionRecorder(
        SessionStore(tmp_path / "sessions", project_root=tmp_path)
    )
    recorder.record_event(
        ToolResultStructuredEvent(
            "tool_result", 1, ToolResultBlock("cold-evidence-call", body, status="ok")
        )
    )
    policy = ContextPolicy(
        physical_window=14048,
        output_reserve=2048,
        headroom_tokens=0,
        evidence_token_budget=2000,
    )
    old: list[AgentMessage] = [
        UserMessage(
            content="Recover RECOVERY_SECRET from cold-evidence-call. Return the exact value."
        ),
        AssistantMessage(
            content=[
                ToolCallContent(
                    id="cold-evidence-call", name="read_evidence", arguments={}
                )
            ]
        ),
        ToolResultMessage(
            tool_call_id="cold-evidence-call", tool_name="read_evidence", content=body
        ),
    ]
    rollover = ContextWindowRollover()
    fresh = rollover.rollover_messages(old, policy=policy)
    recorder.record_context_window_reset(
        window_id=rollover.last_window_id or "cold-recovery",
        messages_before=len(old),
        messages_after=len(fresh),
        replacement=fresh,
    )
    reopened = SessionStore(tmp_path / "sessions", project_root=tmp_path)
    reopened.resume(recorder.store.session_id)
    history = SessionHistory(
        reopened.sessions_dir, artifacts_dir=reopened.artifacts_dir
    )
    history.set_session_id(reopened.session_id)
    manager = ContextManager()
    manager.complete_rollover(fresh, reason="manual")
    requests: list[dict[str, object]] = []
    events: list[AgentEvent] = []
    result = await Agent(
        tools=[ToolSpecAdapter(spec) for spec in build_history_tools(history)],
        model=provider,
    ).run(
        [
            UserMessage(
                content=(
                    "Continue the original recovery task after a window change and process restart. "
                    "The exact value is omitted from your working context. Use history search "
                    "for cold-evidence-call, then read exact pages with max_chars at most 2000. "
                    "Do not guess. Return only the exact RECOVERY_SECRET value."
                )
            )
        ],
        AgentLoopConfig(
            provider=provider,
            context_policy=policy,
            max_llm_calls=20,
            before_provider_request=lambda assembly: requests.append(_trace(assembly)),
            rollover_context=lambda messages: rollover.rollover_messages(
                messages, policy=policy
            ),
        ),
        context_manager=manager,
        emit=events.append,
    )
    _save(tmp_path / "luna-cold-recovery.json", requests, [result], events)
    assert result.termination_reason is TerminationReason.COMPLETED, result.error_detail
    assert secret not in json.dumps(requests[0]["messages"])
    assert (
        requests[0]["context_snapshot"]["current_window_id"] == rollover.last_window_id
    )
    assert secret in "\n".join(
        block.text
        for message in result.messages
        if isinstance(message, AssistantMessage)
        for block in message.content
        if isinstance(block, TextContent)
    )
    assert any(
        isinstance(event, ToolExecutionEndEvent)
        and event.result is not None
        and event.result.tool_name == "history"
        for event in events
    )
    recovered = history.search("cold-evidence-call")
    assert any(
        json.loads(entry.text).get("data", {}).get("content") == body
        for entry in recovered
    )
