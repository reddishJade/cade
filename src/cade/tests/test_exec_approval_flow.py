"""真实 exec 管线中的终端审批集成验证。"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

pty = pytest.importorskip("pty")
tty = pytest.importorskip("tty")

from cade.ai.events import FinalMessage, ProviderEvent, ToolCall, ToolCallEvent
from cade.ai.providers.base import Provider
from cade.ai.providers.registry import ProviderBundle
from cade.ai.types import StreamOptions, ToolDefinition
from cade.ai.usage import UsageTotals
from cade.cli import exec_cmd
from cade.coding_agent.app import build_app
from cade.harness.config import CadeRuntimeConfig
from cade.main import parse_args


class _ScriptedProvider(Provider):
    """先发出指定工具调用，再给出确定的最终回复。"""

    def __init__(self, tool: str, action_input: dict[str, str]) -> None:
        super().__init__()
        self._model = "stub-exec-approval"
        self._base_url = "http://stub.invalid"
        self._transport = "custom"
        self._thinking = False
        self._context_window = 128_000
        self._tool = tool
        self._action_input = action_input
        self.calls = 0
        self.tool_names: list[str] = []

    @property
    def usage_totals(self) -> UsageTotals:
        return UsageTotals()

    @property
    def cache_hit_rate(self) -> float | None:
        return None

    async def stream(
        self,
        messages: list[dict[str, object]],
        tools: list[ToolDefinition],
        options: StreamOptions | None = None,
        **kwargs: object,
    ) -> AsyncIterator[ProviderEvent]:
        del messages, options, kwargs
        self.tool_names = [tool.name for tool in tools]
        self.calls += 1
        if self.calls == 1:
            yield ToolCallEvent(
                [ToolCall(id="stub-call", name=self._tool, input=self._action_input)]
            )
            yield FinalMessage(content="", stop_reason="tool_use")
        else:
            yield FinalMessage(content="stub complete", stop_reason="end_turn")


@contextmanager
def _terminal_pair(answer: str) -> Iterator[tuple[object, object, int]]:
    master, slave = pty.openpty()
    tty.setraw(slave)
    reader = os.fdopen(os.dup(slave), "r", encoding="utf-8", buffering=1)
    writer = os.fdopen(os.dup(slave), "w", encoding="utf-8", buffering=1)
    os.close(slave)
    try:
        os.write(master, answer.encode("utf-8"))
        yield reader, writer, master
    finally:
        reader.close()
        writer.close()
        os.close(master)


def _read_terminal(master: int) -> str:
    os.set_blocking(master, False)
    chunks: list[bytes] = []
    while True:
        try:
            chunk = os.read(master, 4096)
        except BlockingIOError:
            break
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks).decode("utf-8", errors="replace")


# 失效情形：无审批请求、TTY 回答丢失、拒绝后仍执行、提示污染 JSONL。
@pytest.mark.parametrize("tool_name", ["bash", "write"])
@pytest.mark.parametrize("answer,approved", [("y\n", True), ("n\n", False)])
def test_exec_act_approval_reaches_real_tool_and_final_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tool_name: str,
    answer: str,
    approved: bool,
) -> None:
    action_input = (
        {"command": "touch result.txt"}
        if tool_name == "bash"
        else {"path": "result.txt", "content": "approved\n"}
    )
    provider = _ScriptedProvider(tool_name, action_input)
    monkeypatch.setattr(
        "cade.coding_agent.app.build_provider_bundle",
        lambda _settings: ProviderBundle(llm=provider, llms={"main": provider}),
    )
    args = parse_args(
        [
            "exec",
            "--project-root",
            str(tmp_path),
            "--mode",
            "act",
            "--approval",
            "interactive",
            "--event-format",
            "jsonl",
            "run scripted tool",
        ]
    )

    def builder(root: Path, config: CadeRuntimeConfig, sessions: Path):
        return build_app(root, runtime_config=config, sessions_dir=sessions)

    with _terminal_pair(answer) as (reader, writer, master):
        assert reader.isatty()
        assert writer.isatty()
        monkeypatch.setattr(exec_cmd.sys, "stdin", reader)

        @contextmanager
        def terminal() -> Iterator[tuple[object, object]]:
            yield reader, writer

        monkeypatch.setattr(exec_cmd, "_approval_terminal", terminal)
        status = exec_cmd.run_exec(args, CadeRuntimeConfig(), builder)
        terminal_text = _read_terminal(master)

    payloads = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    result_path = tmp_path / "result.txt"
    assert status == (0 if approved else 5)
    assert result_path.exists() is approved
    assert provider.calls == 2
    assert {"read", "write", "edit", "patch", "bash"}.issubset(provider.tool_names)
    assert "Cade approval (act)" in terminal_text
    assert f"Tool: {tool_name}" in terminal_text
    assert "result.txt" in terminal_text
    assert "Approve once?" in terminal_text
    assert payloads[-1]["type"] == "run.completed"
    assert payloads[-1]["status"] == ("completed" if approved else "approval_denied")
    assert payloads[-1]["answer"] == "stub complete"
    assert any(payload["type"] == "tool.started" for payload in payloads)
    completed = [payload for payload in payloads if payload["type"] == "tool.completed"]
    assert len(completed) == 1
    assert completed[0]["status"] == ("ok" if approved else "error")
    assert completed[0]["approval_denied"] is not approved

    trace = {
        "reproduce": (
            "uv run pytest src/cade/tests/test_exec_approval_flow.py -q --tb=short"
        ),
        "tool": tool_name,
        "approved": approved,
        "terminal": terminal_text,
        "events": payloads,
        "file_exists": result_path.exists(),
    }
    (tmp_path / "approval-trace.json").write_text(
        json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8"
    )
