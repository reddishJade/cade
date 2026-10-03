from __future__ import annotations

import json
from typing import Any

from cade.agent.config import AgentContext, BeforeToolCallContext
from cade.agent.messages import AssistantMessage
from cade.agent.types import ToolCallContent, ToolInput, ToolSpec
from cade.coding_agent.execution_modes import ExecutionModeState
from cade.harness.agent_runtime.result import AgentHarnessResult
from cade.harness.agent_runtime.tool_gate import ToolGate

from .file_refs import FileReference


def _registry(app: object) -> tuple[ToolSpec, ...]:
    raw = getattr(app, "registry", ())
    return tuple(raw) if raw else ()


def run_tool_command(command: str, app: object) -> str:
    parts = command.split(maxsplit=2)
    if len(parts) < 2:
        return "usage: /tool NAME INPUT\n/tool list - show enabled tools"
    tool_name = parts[1]
    registry = _registry(app)

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
    return _execute_tool_via_gate(selected, action_input, getattr(app, "agent", None))


def _tool_list(registry: tuple[ToolSpec, ...]) -> str:
    lines = [f"## Available Tools ({len(registry)})", ""]
    for t in sorted(registry, key=lambda x: x.name):
        lines.append(f"  - `{t.name}`: {t.description[:80]}")
    return "\n".join(lines)


def _resolve_tool(name: str, registry: tuple[ToolSpec, ...]) -> ToolSpec | None:
    return next((t for t in registry if t.name == name), None)


def run_shell_shortcut(command: str, app: object) -> str:
    shell_command = command[1:].strip()
    if not shell_command:
        return "usage: !COMMAND"
    return run_tool_command(f"/tool bash {shell_command}", app)


def _execute_tool_via_gate(
    tool: ToolSpec,
    tool_input: ToolInput,
    agent: object,
) -> str:
    """通过 ToolGate 门控 + ToolSpecAdapter 执行 交互 工具命令。

    保持与 canonical agent loop 一致的权限门控路径：
    ToolGate._precheck_permission → PermissionEngine.decide()（唯一生产调用点）
    ToolSpecAdapter.execute() → handler（纯适配器，不自检权限）

    build_after_tool_hook 不在此处调用，交互 手动工具命令不经过 agent 轮次，
    无 session/audit 上下文，且为用户显式输入而非 LLM 决策，不写入审计日志。
    """
    if agent is None:
        return str(tool.handler(tool_input, None))

    mode_state = ExecutionModeState(initial_mode=getattr(agent, "current_mode", "act"))
    gate = ToolGate(
        mode_state=mode_state,
        user_approval_callback=getattr(agent, "user_approval_callback", None),
        auto_approval_callback=getattr(agent, "auto_approval_callback", None),
        permission_policy=getattr(agent, "permission_policy", None),
        approval_policy=getattr(agent, "approval_policy", "on-request"),
        hook_manager=None,
        audit_logger=None,
        session_id=getattr(agent, "session_id", "interaction"),
        restricted_dirs=getattr(agent, "restricted_dirs", ()),
        hook_constraint_providers=getattr(agent, "hook_constraint_providers", ()),
        project_root=getattr(agent, "project_root", None),
        external_directories=getattr(agent, "external_directories", ()),
        sensitive_path_overrides=getattr(agent, "sensitive_path_overrides", ()),
    )
    snapshot = gate.snapshot_for((tool,))
    before_hook = gate.build_before_tool_hook(snapshot)

    ctx = BeforeToolCallContext(
        assistant_message=AssistantMessage(content=[]),
        tool_call=ToolCallContent(
            id="interaction", name=tool.name, arguments=dict(tool_input)
        ),
        args=tool_input,
        context=AgentContext(),
    )
    before_result = before_hook(ctx, None)
    if before_result is not None:
        return before_result.reason or f"tool {tool.name} was blocked"

    content = tool.handler(dict(tool_input), None)
    return str(content)


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
        isinstance(required, list)
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
