"""exec 非交互调用协议测试。"""

from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from cade.agent.results import TerminationReason
from cade.ai.events import ProviderFailure
from cade.harness.agent_runtime.events import (
    FinalStructuredEvent,
    TextDeltaStructuredEvent,
    ToolResultBlock,
    ToolResultStructuredEvent,
    UsageUpdateStructuredEvent,
)
from cade.harness.agent_runtime.result import AgentHarnessResult
from cade.harness.config import CadeRuntimeConfig
from cade.main import parse_args


class _SessionStore:
    session_id = "session-test"
    current_path = Path("/tmp/session-session-test.jsonl")

    def __init__(self) -> None:
        self.records: list[tuple[str, dict[str, object]]] = []

    def append(self, record_type: str, content: dict[str, object]) -> None:
        self.records.append((record_type, content))


class _App:
    def __init__(self, events: list[object]) -> None:
        self.session_store = _SessionStore()
        self.agent = SimpleNamespace(
            provider=SimpleNamespace(
                model="gpt-5.6-luna",
                transport="openai_codex",
                base_url="https://chatgpt.com/backend-api",
                context_window=272000,
            ),
            interrupt=lambda _reason: True,
        )
        self._events = events
        self.closed = False

    def get_model_info(self) -> dict[str, str]:
        return {
            "model": "gpt-5.6-luna",
            "transport": "openai_codex",
            "base_url": "https://chatgpt.com/backend-api",
            "profile": "main",
            "thinking": "on",
            "reasoning_effort": "max",
        }

    def ask_stream(self, _prompt: str):
        return iter(self._events)

    def close(self) -> None:
        self.closed = True


def _final_result(
    reason: TerminationReason = TerminationReason.COMPLETED,
    *,
    failure: ProviderFailure | None = None,
) -> AgentHarnessResult:
    return AgentHarnessResult(
        answer="done",
        messages=[],
        steps=2,
        tool_calls=[],
        termination_reason=reason,
        metrics={"llm_calls": 2, "tool_calls": 1},
        provider_failure=failure,
    )


def test_parse_exec_supports_direct_runtime_overrides(tmp_path: Path) -> None:
    args = parse_args(
        [
            "exec",
            "--project-root",
            str(tmp_path),
            "--model",
            "gpt-5.6-luna",
            "--transport",
            "openai-codex",
            "--reasoning-effort",
            "max",
            "--mode",
            "build",
            "--approval",
            "auto-review",
            "--max-steps",
            "60",
            "--max-llm-calls",
            "80",
            "--timeout",
            "45m",
            "--prompt-file",
            "task.md",
        ]
    )

    assert args.command == "exec"
    assert args.model == "gpt-5.6-luna"
    assert args.transport == "openai_codex"
    assert args.max_steps == 60
    assert args.max_llm_calls == 80
    assert args.timeout == 2700
    assert args.prompt_file == Path("task.md")


def test_exec_argument_errors_use_validation_exit_code() -> None:
    with pytest.raises(SystemExit) as exc_info:
        parse_args(["exec", "--max-steps", "0", "fix it"])

    assert exc_info.value.code == 6


def test_exec_preserves_global_options_before_subcommand(tmp_path: Path) -> None:
    args = parse_args(
        [
            "--project-root",
            str(tmp_path),
            "--config",
            "custom.json",
            "--continue",
            "exec",
            "fix it",
        ]
    )

    assert args.project_root == tmp_path
    assert args.config == Path("custom.json")
    assert args.continue_ is True


def test_exec_resume_alias_maps_to_session() -> None:
    args = parse_args(["exec", "resume", "run-1", "--prompt-file", "-"])

    assert args.command == "exec"
    assert args.session == "run-1"
    assert args.prompt_file == Path("-")


def test_exec_emits_only_json_lines_and_returns_completion_code(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    from cade.cli.exec_cmd import run_exec

    final = _final_result()
    events = [
        TextDeltaStructuredEvent(type="text_delta", step=1, data="done"),
        UsageUpdateStructuredEvent(
            type="usage_update",
            step=1,
            data={"input_tokens": 120443, "output_tokens": 100},
        ),
        FinalStructuredEvent(type="final", step=2, data=final),
    ]
    app = _App(events)
    args = parse_args(
        [
            "exec",
            "--project-root",
            str(tmp_path),
            "--mode",
            "build",
            "--prompt-file",
            "-",
        ]
    )
    monkeypatch.setattr("cade.cli.exec_cmd.sys.stdin.read", lambda: "fix it")

    def build(*_args: object) -> _App:
        print("provider diagnostic")
        return app

    status = run_exec(args, CadeRuntimeConfig(), build)

    captured = capsys.readouterr()
    payloads = [json.loads(line) for line in captured.out.splitlines()]
    assert status == 0
    assert [payload["type"] for payload in payloads] == [
        "run.started",
        "config.resolved",
        "budget.updated",
        "run.completed",
    ]
    assert payloads[0]["session_id"] == "session-test"
    assert payloads[1]["context_window"] == 272000
    assert payloads[1]["token_budget"] == 255616
    assert payloads[2]["estimated_tokens"] == 120443
    assert payloads[-1]["exit_code"] == 0
    assert payloads[-1]["answer"] == "done"
    assert app.closed
    assert "provider diagnostic" in captured.err
    assert app.session_store.records[-1][1]["type"] == "exec_result"


def test_exec_full_event_detail_preserves_raw_events(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    from cade.cli.exec_cmd import run_exec

    app = _App(
        [
            TextDeltaStructuredEvent(type="text_delta", step=1, data="done"),
            FinalStructuredEvent(type="final", step=1, data=_final_result()),
        ]
    )
    args = parse_args(
        [
            "exec",
            "--project-root",
            str(tmp_path),
            "--event-detail",
            "full",
            "--approval",
            "never",
            "--prompt-file",
            "-",
        ]
    )
    monkeypatch.setattr("cade.cli.exec_cmd.sys.stdin.read", lambda: "fix it")

    assert run_exec(args, CadeRuntimeConfig(), lambda *_args: app) == 0

    payloads = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert payloads[2]["type"] == "text_delta"
    assert payloads[2]["data"] == "done"


def test_exec_compact_tool_result_is_bounded(tmp_path: Path, capsys) -> None:
    from cade.cli.exec_cmd import run_exec

    result = ToolResultStructuredEvent(
        type="tool_result",
        step=1,
        data=ToolResultBlock(
            tool_use_id="call-1",
            content="x" * 5000,
            status="ok",
        ),
    )
    app = _App(
        [result, FinalStructuredEvent(type="final", step=1, data=_final_result())]
    )
    args = parse_args(
        ["exec", "--project-root", str(tmp_path), "--approval", "never", "fix it"]
    )

    assert run_exec(args, CadeRuntimeConfig(), lambda *_args: app) == 0

    payloads = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    completed = next(item for item in payloads if item["type"] == "tool.completed")
    assert len(completed["content"]) == 4000
    assert completed["content_chars"] == 5000
    assert completed["content_truncated"] is True


def test_exec_compact_tool_failure_has_error_status(tmp_path: Path, capsys) -> None:
    from cade.cli.exec_cmd import run_exec

    result = ToolResultStructuredEvent(
        type="tool_result",
        step=1,
        data=ToolResultBlock(
            tool_use_id="call-1",
            content="exit code: 127",
            status="error",
        ),
    )
    app = _App(
        [result, FinalStructuredEvent(type="final", step=1, data=_final_result())]
    )
    args = parse_args(
        ["exec", "--project-root", str(tmp_path), "--approval", "never", "fix it"]
    )

    assert run_exec(args, CadeRuntimeConfig(), lambda *_args: app) == 0

    payloads = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    completed = next(item for item in payloads if item["type"] == "tool.completed")
    assert completed["status"] == "error"


def test_exec_maps_request_budget_failure_to_exit_four(tmp_path: Path, capsys) -> None:
    from cade.cli.exec_cmd import run_exec

    failure = ProviderFailure(
        message="request too large",
        exception_type="RequestBudgetExceededError",
        status_code=413,
    )
    final = _final_result(TerminationReason.PROVIDER_ERROR, failure=failure)
    app = _App([FinalStructuredEvent(type="final", step=1, data=final)])
    args = parse_args(
        [
            "exec",
            "--project-root",
            str(tmp_path),
            "--approval",
            "never",
            "fix it",
        ]
    )

    status = run_exec(args, CadeRuntimeConfig(), lambda *_args: app)

    payload = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert status == 4
    assert payload["status"] == "provider_error"
    assert payload["error"]["status_code"] == 413


def test_exec_rejects_interactive_approval_without_tty(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    from cade.cli.exec_cmd import run_exec

    args = parse_args(
        [
            "exec",
            "--project-root",
            str(tmp_path),
            "--approval",
            "interactive",
            "fix it",
        ]
    )
    monkeypatch.setattr("cade.cli.exec_cmd.sys.stdin.isatty", lambda: False)
    built = False

    def build(*_args: object) -> object:
        nonlocal built
        built = True
        return object()

    status = run_exec(args, CadeRuntimeConfig(), build)

    payload = json.loads(capsys.readouterr().out)
    assert status == 6
    assert payload["status"] == "validation_error"
    assert "requires a TTY" in payload["error"]["message"]
    assert not built


def test_exec_deny_stops_on_first_permission_denial(tmp_path: Path, capsys) -> None:
    from cade.cli.exec_cmd import run_exec

    denied = ToolResultStructuredEvent(
        type="tool_result",
        step=1,
        data=ToolResultBlock(
            tool_use_id="call-1",
            content="blocked",
            status="error",
            permission_notice="approval required",
        ),
    )
    app = _App([denied])
    args = parse_args(
        [
            "exec",
            "--project-root",
            str(tmp_path),
            "--approval",
            "deny",
            "fix it",
        ]
    )

    status = run_exec(args, CadeRuntimeConfig(), lambda *_args: app)

    payload = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert status == 5
    assert payload["status"] == "approval_denied"
    assert payload["exit_code"] == 5


def test_exec_reports_only_files_changed_during_run(tmp_path: Path, capsys) -> None:
    from cade.cli.exec_cmd import run_exec

    tracked = tmp_path / "tracked.txt"
    existing = tmp_path / "existing.txt"
    tracked.write_text("before", encoding="utf-8")
    existing.write_text("unchanged", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)

    final = _final_result()

    class _ChangingApp(_App):
        def ask_stream(self, _prompt: str):
            tracked.write_text("after with a different size", encoding="utf-8")
            internal = tmp_path / ".cade" / "session_artifacts" / "result.txt"
            internal.parent.mkdir(parents=True)
            internal.write_text("internal", encoding="utf-8")
            yield FinalStructuredEvent(type="final", step=1, data=final)

    app = _ChangingApp([])
    args = parse_args(
        [
            "exec",
            "--project-root",
            str(tmp_path),
            "--approval",
            "never",
            "fix it",
        ]
    )

    assert run_exec(args, CadeRuntimeConfig(), lambda *_args: app) == 0

    payload = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert payload["changed_files"] == ["tracked.txt"]


def test_exec_timeout_interrupts_run_and_returns_124(tmp_path: Path, capsys) -> None:
    from cade.cli.exec_cmd import run_exec

    interrupted = threading.Event()
    app = _App([])

    def interrupt(_reason: str) -> bool:
        interrupted.set()
        return True

    app.agent.interrupt = interrupt

    def wait_for_interrupt(_prompt: str):
        interrupted.wait(timeout=1)
        return iter(())

    app.ask_stream = wait_for_interrupt
    args = parse_args(
        [
            "exec",
            "--project-root",
            str(tmp_path),
            "--approval",
            "never",
            "--timeout",
            "0.02s",
            "fix it",
        ]
    )

    status = run_exec(args, CadeRuntimeConfig(), lambda *_args: app)

    payload = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert interrupted.is_set()
    assert status == 124
    assert payload["status"] == "timed_out"
    assert payload["exit_code"] == 124
