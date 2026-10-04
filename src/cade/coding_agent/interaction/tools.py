from __future__ import annotations

import json
from typing import Any

from cade.agent.types import TextContent, ToolInput, ToolSpec
from cade.harness.agent_runtime.result import AgentHarnessResult

from .app_contract import InteractionApp
from .file_refs import FileReference


def run_tool_command(command: str, app: InteractionApp) -> str:
    parts = command.split(maxsplit=2)
    if len(parts) < 2:
        return "usage: /tool NAME INPUT\n/tool list - show enabled tools"
    tool_name = parts[1]
    registry = app.agent.enabled_tools

    if tool_name == "list":
        return _tool_list(registry)

    # ── Direct execution ──
    selected = _resolve_tool(tool_name, registry)

    if selected is None:
        return f"unknown tool: {tool_name}"
    raw_input = parts[2] if len(parts) == 3 else ""
    try:
        action_input = parse_tool_input(selected, raw_input)
    except ValueError as exc:
        return str(exc)
    result = app.agent.execute_tool(selected, action_input)
    return "\n".join(
        block.text for block in result.content if isinstance(block, TextContent)
    )


def _tool_list(registry: tuple[ToolSpec, ...]) -> str:
    lines = [f"## Available Tools ({len(registry)})", ""]
    for t in sorted(registry, key=lambda x: x.name):
        lines.append(f"  - `{t.name}`: {t.description[:80]}")
    return "\n".join(lines)


def _resolve_tool(name: str, registry: tuple[ToolSpec, ...]) -> ToolSpec | None:
    return next((t for t in registry if t.name == name), None)


def run_shell_shortcut(command: str, app: InteractionApp) -> str:
    shell_command = command[1:].strip()
    if not shell_command:
        return "usage: !COMMAND"
    return run_tool_command(f"/tool bash {shell_command}", app)


def parse_tool_input(tool: ToolSpec, raw_input: str) -> ToolInput:
    """解析 `/tool` 命令的人类输入；核心工具协议只接收 dict。"""
    text = raw_input.strip()
    if text.startswith(("{", "[")):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON input: {exc.msg}") from exc
        if not isinstance(data, dict):
            raise ValueError("JSON input must be an object")
        return data
    key = cli_shorthand_key(tool)
    return {key: text} if key else {}


def cli_shorthand_key(tool: ToolSpec) -> str:
    schema = tool.schema or {}
    required = schema.get("required")
    if (
        isinstance(required, (list, tuple))
        and len(required) == 1
        and isinstance(required[0], str)
    ):
        return required[0]
    return "input"


def final_stop_reason(data: AgentHarnessResult) -> str | None:
    if data.termination_reason.value == "step_limit":
        return "[stopped] step limit reached"
    if data.termination_reason.value == "llm_call_limit":
        return "[stopped] LLM call limit reached"
    if data.termination_reason.value == "watchdog":
        reason = data.watchdog_reason or "repeated tool calls detected"
        return f"[stopped] {reason}"
    if data.termination_reason.value == "cancelled":
        return "[stopped] cancelled"
    if data.termination_reason.value == "provider_error":
        reason = data.error_detail or "provider error"
        return f"[stopped] {reason}"
    return None


def file_reference_event(references: list[FileReference]) -> dict[str, Any]:
    return {
        "type": "file_references",
        "data": [
            {
                "path": reference.path,
                "status": reference.status,
                "error": reference.error,
            }
            for reference in references
        ],
    }
