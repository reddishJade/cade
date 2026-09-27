"""预算 E2E：真实 SDK/HTTP/工具链，以及可选的 Luna high 实际调用。

复现：CADE_LIVE_CONTEXT_E2E=1 uv run pytest src/cade/tests/test_context_budget_e2e.py
-q --basetemp=/tmp/cade-context-budget-e2e。请求与结果只保存到 pytest 临时目录。
本地 HTTP 用于注入真实服务难以稳定触发的超限响应，不冒充模型验证。
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

import cade.agent.context as context_module
from cade.agent.agent import Agent
from cade.agent.config import AgentLoopConfig, ContextWindowResetReason
from cade.agent.context_manager import ContextManager
from cade.agent.events import AgentEvent
from cade.agent.messages import AgentMessage, SystemMessage, UserMessage
from cade.agent.request import RequestAssembly
from cade.agent.results import AgentLoopResult, TerminationReason
from cade.agent.types import ToolInput, ToolSpec, ToolSpecAdapter
from cade.ai.providers.openai import OpenAIChatProvider
from cade.ai.providers.responses import (
    OpenAICodexResponsesProvider,
    OpenAIResponsesProvider,
)
from cade.ai.types import ProviderConfig
from cade.harness.agent_runtime.context_window import ContextWindowRollover
from cade.harness.auth.manager import AuthManager


def _trace(assembly: RequestAssembly) -> dict[str, object]:
    return {
        "messages": list(assembly.wire_messages),
        "tokens": assembly.estimated_tokens,
        "local_tokens": assembly.local_estimated_tokens,
        "source": assembly.token_estimate_source,
        "budget": assembly.token_budget,
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

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass

        def do_POST(self) -> None:
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(payload)
            index = len(requests) - 1
            tool_call = scenario == "tool-overflow" and index == 0
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
                            "id": "read-evidence-1",
                            "type": "function",
                            "function": {"name": "read_evidence", "arguments": "{}"},
                        }
                    ]
                }
            prompt_tokens = 0 if scenario == "lifecycle" and index == 1 else 100
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
            if scenario == "responses-code"
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
        assert "EXACT_EVIDENCE" not in json.dumps(http_requests[2])
        assert "read-evidence-1" in json.dumps(http_requests[2])


async def test_request_anchor_lifecycle_http_e2e(tmp_path: Path) -> None:
    """真实请求覆盖零用量、失败、前缀追加、工具/模型变化和投影替换。"""
    manager = ContextManager()
    assemblies: list[dict[str, object]] = []
    results: list[AgentLoopResult] = []
    events: list[AgentEvent] = []
    prefix = [SystemMessage(content="original instructions")]
    with _http_provider("lifecycle") as (provider, http_requests):
        agent = Agent(tools=[], model=provider)
        config = AgentLoopConfig(
            provider=provider,
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
        await run()  # 服务端报告零输入，不更新有效锚点。
        await run()
        await run()  # 普通 HTTP 400 失败，不更新有效锚点。
        await run()
        prefix = [SystemMessage(content="revised instructions")]
        await run()
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
        "provider_anchor",
        "local",
        "local",
        "local",
        "local",
    ]
    assert int(assemblies[2]["tokens"]) >= 100
    assert results[3].termination_reason is TerminationReason.PROVIDER_ERROR
    assert all(
        result.termination_reason is TerminationReason.COMPLETED
        for index, result in enumerate(results)
        if index != 3
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
