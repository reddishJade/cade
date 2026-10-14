"""Hook 发射与工具回调组织。"""

from __future__ import annotations

from ...agent.types import AgentToolResult, TextContent, ToolCallContent
from ..observability import HookCorrelationFields, HookManager, HookRecord


def emit_hook(
    hook_manager: HookManager | None,
    record: HookRecord,
) -> None:
    if hook_manager is not None:
        hook_manager.emit(record)


def emit_tool_hook(
    hook_manager: HookManager | None,
    tool_call: ToolCallContent,
    result: AgentToolResult,
    action_input: str,
    result_text: str,
    correlation: HookCorrelationFields,
) -> None:
    if result.is_error:
        emit_hook(
            hook_manager,
            HookRecord(
                "on_error",
                tool=tool_call.name,
                input=action_input,
                error=result_text,
                **correlation,
            ),
        )
        return
    emit_hook(
        hook_manager,
        HookRecord(
            "post_tool",
            tool=tool_call.name,
            input=action_input,
            output=result_text,
            **correlation,
        ),
    )


def tool_result_text(result: AgentToolResult) -> str:
    if not result.content:
        return ""
    return "".join(c.text for c in result.content if isinstance(c, TextContent))
