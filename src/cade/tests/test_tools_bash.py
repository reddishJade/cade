"""bash 工具请求解析纯函数单元测试。"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

import pytest

from cade.agent.types import TerminalRenderIntent
from cade.coding_agent.tools.bash import (
    _build_bash_execution_plan,
    _parse_bash_request,
    _parse_timeout,
    build_bash_tool,
)
from cade.coding_agent.tools.shell_adapter import ShellSpec
from cade.harness.execution_env import ExecutionResult


class _RecordingShell:
    def __init__(self, result: ExecutionResult | None = None) -> None:
        self.argv: list[str] = []
        self.cwd: Path | None = None
        self.timeout = 0
        self.result = result or ExecutionResult(stdout="local output")

    def run(
        self,
        argv: list[str],
        cwd: Path,
        timeout: int = 30_000,
        cancel_event: threading.Event | None = None,
        on_progress: Callable[[str], None] | None = None,
        env: dict[str, str] | None = None,
    ) -> ExecutionResult:
        del cancel_event, env
        self.argv = argv
        self.cwd = cwd
        self.timeout = timeout
        if on_progress is not None:
            on_progress("local output")
        return self.result


class TestParseBashRequest:
    def test_missing_command_raises(self) -> None:
        with pytest.raises(ValueError, match="command"):
            _parse_bash_request({})

    def test_input_alias_is_not_accepted(self) -> None:
        with pytest.raises(ValueError, match="command"):
            _parse_bash_request({"input": "echo legacy"})

    def test_unrecognized_purpose_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="purpose must be validation"):
            _parse_bash_request({"command": "echo hi", "purpose": "exploration"})


class TestParseTimeout:
    def test_seconds_parameter_is_not_accepted(self) -> None:
        with pytest.raises(ValueError, match="unsupported bash parameter"):
            _parse_timeout({"timeout": 60})

    def test_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="must be positive"):
            _parse_timeout({"timeout_ms": -1})

    def test_too_large_raises(self) -> None:
        with pytest.raises(ValueError, match="<= 300000"):
            _parse_timeout({"timeout_ms": 999999})

    def test_non_int_raises(self) -> None:
        with pytest.raises(ValueError, match="must be an integer"):
            _parse_timeout({"timeout_ms": "abc"})


def test_bash_accepts_absolute_workdir_inside_project(tmp_path: Path) -> None:
    nested = tmp_path / "packages" / "core"

    plan = _build_bash_execution_plan(
        _parse_bash_request({"command": "pwd", "workdir": str(nested)}),
        tmp_path,
        command_prefix=None,
        spawn_hook=None,
    )

    assert plan.cwd == nested.resolve()


def test_bash_tool_depends_directly_on_local_shell(tmp_path: Path) -> None:
    shell = _RecordingShell()
    tool = build_bash_tool(
        tmp_path,
        shell_spec=ShellSpec("sh", ("sh", "-c"), "posix"),
        shell=shell,
    )

    output = tool.handler(
        {"command": "printf local", "timeout_ms": 1234},
        None,
    )

    assert output == "local output"
    assert shell.argv == ["sh", "-c", "printf local"]
    assert shell.cwd == tmp_path.resolve()
    assert shell.timeout == 1234
    assert "timeout" not in (tool.schema or {})["properties"]
    assert output.render_intent == TerminalRenderIntent(
        command="printf local",
        cwd=tmp_path.resolve().as_posix(),
    )


def test_bash_nonzero_exit_is_a_structured_tool_failure(tmp_path: Path) -> None:
    shell = _RecordingShell(ExecutionResult(stderr="missing", returncode=127))
    tool = build_bash_tool(
        tmp_path,
        shell_spec=ShellSpec("sh", ("sh", "-c"), "posix"),
        shell=shell,
    )

    output = tool.handler({"command": "missing-tool"}, None)

    assert output.is_error
    assert output.metadata["exit_code"] == 127
    assert "exit code: 127" in output


def test_bash_validation_purpose_is_explicit_metadata(tmp_path: Path) -> None:
    shell = _RecordingShell(ExecutionResult(returncode=0))
    tool = build_bash_tool(
        tmp_path,
        shell_spec=ShellSpec("sh", ("sh", "-c"), "posix"),
        shell=shell,
    )

    output = tool.handler({"command": "pytest -q", "purpose": "validation"}, None)

    assert output.metadata["purpose"] == "validation"
    assert output.metadata["exit_code"] == 0
    assert (tool.schema or {})["properties"]["purpose"]["enum"] == ["validation"]


def test_bash_timeout_is_a_structured_tool_failure(tmp_path: Path) -> None:
    shell = _RecordingShell(ExecutionResult(timed_out=True, returncode=0))
    tool = build_bash_tool(
        tmp_path,
        shell_spec=ShellSpec("sh", ("sh", "-c"), "posix"),
        shell=shell,
    )

    output = tool.handler({"command": "long-running"}, None)

    assert output.is_error
    assert output.metadata["timed_out"] is True
