"""bash 工具：命令执行、workdir、timeout_ms、流式输出。"""

from __future__ import annotations

import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from cade.agent.types import TerminalRenderIntent, ToolInput, ToolOutput, ToolSpec
from cade.coding_agent.validation import workspace_fingerprint
from cade.harness.execution_env import (
    ExecutionResult,
    Shell,
    SubprocessShell,
)

from ._constants import DEFAULT_TIMEOUT_SECONDS
from .path_utils import resolve_project_path
from .shell_adapter import (
    ShellSpec,
    build_shell_argv,
    build_shell_environment,
    detect_shell,
)
from .truncate import DEFAULT_MAX_BYTES, truncate_tail


@dataclass(frozen=True)
class BashRequest:
    command: str
    timeout: int  # 毫秒
    workdir: str | None = None
    purpose: Literal["validation"] | None = None


# 最大 timeout_ms 值（300 秒）
_MAX_TIMEOUT_MS = 300_000


def build_bash_tool(
    project_root: Path,
    cancel_event: threading.Event | None = None,
    shell_spec: ShellSpec | None = None,
    shell: Shell | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> ToolSpec:
    root = project_root.resolve()
    spec = shell_spec or detect_shell()
    shell = shell or SubprocessShell()
    shell_syntax = spec.syntax

    def bash(data: ToolInput, _on_update: Callable[[str], None] | None = None) -> str:
        request = _parse_bash_request(data)
        cwd = _resolve_workdir(root, request.workdir)
        before = (
            workspace_fingerprint(root) if request.purpose == "validation" else None
        )
        result = shell.run(
            build_shell_argv(spec, request.command),
            cwd=cwd,
            timeout=request.timeout,
            cancel_event=cancel_event,
            on_progress=on_progress,
            env=build_shell_environment(spec),
        )
        output = _render_bash_output(result, request.timeout)
        return ToolOutput(
            output,
            metadata={
                "exit_code": result.returncode,
                "timed_out": result.timed_out,
                "cancelled": result.cancelled,
                **({"purpose": request.purpose} if request.purpose is not None else {}),
                **(
                    {
                        "validation_state": {
                            "before": before,
                            "after": workspace_fingerprint(root),
                        }
                    }
                    if request.purpose == "validation"
                    else {}
                ),
            },
            is_error=result.returncode != 0 or result.timed_out or result.cancelled,
            render_intent=TerminalRenderIntent(
                command=request.command,
                cwd=cwd.as_posix(),
            ),
        )

    return ToolSpec(
        name="bash",
        description="Run a shell command.",
        input_hint='JSON: {"command": "git status --short", "timeout_ms": 30000}',
        handler=bash,
        prompt_guidelines=(
            ("PowerShell 5.1 does not support `&&`; use `;` to separate commands.",)
            if spec.ps_kind == "powershell"
            else ()
        ),
        schema=_build_schema(shell_syntax),
    )


# ── 参数定义 ──


def _build_schema(shell_syntax: str) -> dict[str, Any]:
    """按 shell 类型生成参数 Schema。"""
    props: dict[str, Any] = {
        "command": {
            "type": "string",
            "description": "Shell command to run.",
        },
        "timeout_ms": {
            "type": "integer",
            "description": "Timeout in milliseconds. Default 30000. Max 300000.",
            "minimum": 1,
            "maximum": _MAX_TIMEOUT_MS,
        },
        "workdir": {
            "type": "string",
            "description": (
                "Working directory inside the project root, as a relative or absolute "
                "path. Defaults to project root."
            ),
        },
        "purpose": {
            "type": "string",
            "enum": ["validation"],
            "description": (
                "Set to validation only for a check whose outcome should appear "
                "in the run's validation record."
            ),
        },
    }
    return {
        "type": "object",
        "properties": props,
        "required": ["command"],
        "additionalProperties": False,
    }


# ── 请求解析 ──


def _parse_bash_request(data: ToolInput) -> BashRequest:
    command = str(data.get("command") or "").strip()
    if not command:
        raise ValueError("command is required")
    return BashRequest(
        command=command,
        timeout=_parse_timeout(data),
        workdir=_parse_workdir(data),
        purpose=_parse_purpose(data),
    )


def _parse_purpose(data: ToolInput) -> Literal["validation"] | None:
    """仅接受显式验证意图，避免从命令文本推断。"""
    purpose = data.get("purpose")
    if purpose is None:
        return None
    if purpose != "validation":
        raise ValueError("purpose must be validation")
    return "validation"


def _parse_workdir(data: ToolInput) -> str | None:
    raw = data.get("workdir")
    if raw is None:
        return None
    workdir = str(raw).strip()
    return workdir if workdir else None


def _parse_timeout(data: ToolInput) -> int:
    """解析唯一受支持的毫秒级超时参数。"""
    if "timeout" in data:
        raise ValueError("unsupported bash parameter: timeout")
    timeout_ms = data.get("timeout_ms", DEFAULT_TIMEOUT_SECONDS * 1000)
    try:
        value = int(timeout_ms)
    except (TypeError, ValueError) as exc:
        raise ValueError("timeout_ms must be an integer") from exc
    if value <= 0:
        raise ValueError("timeout_ms must be positive")
    if value > _MAX_TIMEOUT_MS:
        raise ValueError(f"timeout_ms must be <= {_MAX_TIMEOUT_MS}")
    return value


def _resolve_workdir(root: Path, workdir: str | None) -> Path:
    """安全解析 workdir。"""
    if workdir is None:
        return root
    return resolve_project_path(root, workdir)


# ── 输出渲染 ──


def _render_bash_output(
    result: ExecutionResult,
    timeout_ms: int,
) -> str:
    if result.full_output_path is None:
        output = _truncate_bash_output(result.output)
    else:
        truncated = truncate_tail(result.output)
        output = (
            f"{truncated.content}\n"
            f"[Showing the last {DEFAULT_MAX_BYTES // 1024}KB of "
            f"{result.total_output_lines} lines. "
            f"Full output: {result.full_output_path}]"
        )

    if result.timed_out:
        output += f"\nCommand timed out after {timeout_ms}ms"
    elif result.cancelled:
        output += "\nCommand cancelled"
    elif result.returncode not in (0, None):
        output = f"exit code: {result.returncode}\n{output}"
    return output


def _truncate_bash_output(output: str) -> str:
    """从唯一的完整输出生成有界尾部，超限时保存完整日志。"""
    if not output:
        return "(no output)"
    truncated = truncate_tail(output)
    if not truncated.truncated:
        return truncated.content
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="",
        prefix="cade-bash-",
        suffix=".log",
        delete=False,
    ) as stream:
        stream.write(output)
        path = stream.name
    return (
        f"{truncated.content}\n"
        f"[Showing {truncated.output_lines} of {truncated.total_lines} lines"
        f" ({DEFAULT_MAX_BYTES // 1024}KB limit). Full output: {path}]"
    )
