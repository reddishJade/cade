"""exec 非交互调用协议测试。"""

from __future__ import annotations

import json
import subprocess
import threading
from contextlib import nullcontext
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from cade.agent.results import TerminationReason
from cade.agent.types import ToolSpec
from cade.ai.events import ProviderFailure, ToolCall
from cade.coding_agent.execution_modes import build_default_mode_rulesets
from cade.harness.agent_runtime.events import (
    FinalStructuredEvent,
    TextDeltaStructuredEvent,
    ToolResultBlock,
    ToolResultStructuredEvent,
    ToolUseStructuredEvent,
    UsageUpdateStructuredEvent,
)
from cade.harness.agent_runtime.result import AgentHarnessResult
from cade.harness.config import CadeRuntimeConfig
from cade.harness.security.permissions import PermissionEngine, PermissionEngineConfig
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
    assert payloads[1]["token_budget"] == 250176
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


def test_exec_records_explicit_validation_results(tmp_path: Path, capsys) -> None:
    from cade.cli.exec_cmd import run_exec

    events: list[object] = []
    for call_id, command, purpose, status, exit_code in (
        ("check-1", "pytest -q", "validation", "ok", 0),
        ("check-2", "ruff check src", "validation", "error", 1),
        ("explore-1", "ls", None, "ok", 0),
    ):
        args: dict[str, object] = {"command": command}
        if purpose is not None:
            args["purpose"] = purpose
        events.append(
            ToolUseStructuredEvent(
                type="tool_use",
                step=1,
                data=ToolCall(id=call_id, name="bash", input=args),
            )
        )
        events.append(
            ToolResultStructuredEvent(
                type="tool_result",
                step=1,
                data=ToolResultBlock(
                    tool_use_id=call_id,
                    content="output",
                    status=status,
                    exit_code=exit_code,
                ),
            )
        )
    events.append(FinalStructuredEvent(type="final", step=2, data=_final_result()))
    app = _App(events)
    args = parse_args(
        ["exec", "--project-root", str(tmp_path), "--approval", "never", "fix it"]
    )

    assert run_exec(args, CadeRuntimeConfig(), lambda *_args: app) == 0

    payloads = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    completed = next(item for item in payloads if item["type"] == "run.completed")
    assert completed["validation"] == [
        {"tool_call_id": "check-1", "status": "passed", "exit_code": 0},
        {"tool_call_id": "check-2", "status": "failed", "exit_code": 1},
    ]
    assert (
        app.session_store.records[-1][1]["data"]["validation"]
        == completed["validation"]
    )


def test_exec_records_unfinished_validation(tmp_path: Path, capsys) -> None:
    from cade.cli.exec_cmd import run_exec

    app = _App(
        [
            ToolUseStructuredEvent(
                type="tool_use",
                step=1,
                data=ToolCall(
                    id="check-1",
                    name="bash",
                    input={"command": "pytest -q", "purpose": "validation"},
                ),
            ),
            FinalStructuredEvent(type="final", step=2, data=_final_result()),
        ]
    )
    args = parse_args(
        ["exec", "--project-root", str(tmp_path), "--approval", "never", "fix it"]
    )

    assert run_exec(args, CadeRuntimeConfig(), lambda *_args: app) == 0

    payloads = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    completed = next(item for item in payloads if item["type"] == "run.completed")
    assert completed["validation"] == [
        {"tool_call_id": "check-1", "status": "incomplete", "exit_code": None}
    ]


def test_validation_does_not_claim_success_without_exit_evidence() -> None:
    from cade.cli.exec_cmd import _ValidationTracker

    tracker = _ValidationTracker()
    for call_id in ("unknown", "blocked"):
        tracker.observe(
            ToolUseStructuredEvent(
                type="tool_use",
                step=1,
                data=ToolCall(
                    id=call_id,
                    name="bash",
                    input={"command": "check", "purpose": "validation"},
                ),
            )
        )
    tracker.observe(
        ToolResultStructuredEvent(
            type="tool_result",
            step=1,
            data=ToolResultBlock(tool_use_id="unknown", content="ok", status="ok"),
        )
    )
    tracker.observe(
        ToolResultStructuredEvent(
            type="tool_result",
            step=1,
            data=ToolResultBlock(
                tool_use_id="blocked",
                content="approval denied",
                status="error",
                approval_denied=True,
            ),
        )
    )

    assert tracker.results() == [
        {"tool_call_id": "unknown", "status": "unknown", "exit_code": None},
        {"tool_call_id": "blocked", "status": "blocked", "exit_code": None},
    ]


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


# 失效情形：Plan 错认人工审批、Act 误放行无 TTY、显式人工路由失效。
@pytest.mark.parametrize(
    ("mode", "router", "expected_status"),
    [
        ("plan", "mode", 0),
        ("build", "mode", 0),
        ("act", "mode", 6),
        ("plan", "user", 6),
        ("plan", "auto", 0),
    ],
)
def test_exec_preflight_uses_mode_reviewer(
    monkeypatch, tmp_path: Path, capsys, mode: str, router: str, expected_status: int
) -> None:
    from cade.cli.exec_cmd import run_exec

    args = parse_args(
        ["exec", "--project-root", str(tmp_path), "--mode", mode, "inspect"]
    )
    config = CadeRuntimeConfig()
    config = config.model_copy(
        update={
            "security": config.security.model_copy(update={"approval_router": router})
        }
    )
    monkeypatch.setattr("cade.cli.exec_cmd.sys.stdin.isatty", lambda: False)
    app = _App([FinalStructuredEvent(type="final", step=1, data=_final_result())])

    status = run_exec(args, config, lambda *_args: app)

    assert status == expected_status
    assert app.closed is (expected_status == 0)
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["exit_code"] == status


# 失效情形：有 TTY 却无 callback、批准未传入权限引擎、拒绝污染 JSONL stdout。
@pytest.mark.parametrize(
    ("tool_name", "action_input", "answer", "expected_decision", "expected_exit"),
    [
        ("write", {"path": "report.txt", "content": "ok"}, "y\n", "allow", 0),
        ("bash", {"command": "touch report.txt"}, "y\n", "allow", 0),
        ("bash", {"command": "touch report.txt"}, "n\n", "deny", 5),
    ],
)
def test_exec_interactive_approval_drives_act_permission_engine(
    monkeypatch,
    tmp_path: Path,
    capsys,
    tool_name: str,
    action_input: dict[str, str],
    answer: str,
    expected_decision: str,
    expected_exit: int,
) -> None:
    from cade.cli.exec_cmd import run_exec

    terminal_output = StringIO()
    monkeypatch.setattr("cade.cli.exec_cmd.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(
        "cade.cli.exec_cmd._approval_terminal",
        lambda: nullcontext((StringIO(answer), terminal_output)),
        raising=False,
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
            "change it",
        ]
    )
    app = _App([])
    decisions: list[str] = []

    def events(_prompt: str):
        engine = PermissionEngine(
            PermissionEngineConfig(
                project_root=tmp_path,
                mode_ruleset=build_default_mode_rulesets(tmp_path)["act"],
                mode_fallback="ask",
                shell_mutation_policy="ask",
                execution_mode="act",
            )
        )
        result = engine.decide(
            tool_name,
            action_input,
            tool_spec=ToolSpec(tool_name, "", "", lambda _data, _update: ""),
            approval_callback=app.agent.user_approval_callback,
            approvals_reviewer="user",
        )
        decisions.append(result.decision)
        if result.blocked:
            yield ToolResultStructuredEvent(
                type="tool_result",
                step=1,
                data=ToolResultBlock(
                    tool_use_id="approval-test",
                    content=result.reason,
                    status="error",
                    approval_denied=True,
                ),
            )
        yield FinalStructuredEvent(type="final", step=2, data=_final_result())

    monkeypatch.setattr(app, "ask_stream", events)

    status = run_exec(args, CadeRuntimeConfig(), lambda *_args: app)

    assert status == expected_exit
    assert decisions == [expected_decision]
    assert "Approve once" in terminal_output.getvalue()
    assert tool_name in terminal_output.getvalue()
    assert "report.txt" in terminal_output.getvalue()
    assert all(
        isinstance(json.loads(line), dict)
        for line in capsys.readouterr().out.splitlines()
    )


def test_exec_deny_stops_on_first_permission_denial(tmp_path: Path, capsys) -> None:
    from cade.cli.exec_cmd import run_exec

    denied = ToolResultStructuredEvent(
        type="tool_result",
        step=1,
        data=ToolResultBlock(
            tool_use_id="call-1",
            content="blocked",
            status="error",
            approval_denied=True,
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


def test_exec_failed_approved_command_is_not_approval_denial(
    tmp_path: Path, capsys
) -> None:
    from cade.cli.exec_cmd import run_exec

    failed_command = ToolResultStructuredEvent(
        type="tool_result",
        step=1,
        data=ToolResultBlock(
            tool_use_id="call-1",
            content="exit code: 1",
            status="error",
            permission_notice=(
                "Automatic approval review approved: checking a permission denied error"
            ),
        ),
    )
    app = _App(
        [
            failed_command,
            FinalStructuredEvent(type="final", step=2, data=_final_result()),
        ]
    )
    args = parse_args(
        ["exec", "--project-root", str(tmp_path), "--approval", "deny", "fix it"]
    )

    status = run_exec(args, CadeRuntimeConfig(), lambda *_args: app)

    payload = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert status == 0
    assert payload["status"] == "completed"
    assert payload["error"] is None


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
