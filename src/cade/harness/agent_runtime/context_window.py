"""可丢弃上下文窗口与活动工作集管理。"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from cade.agent.config import ContextWindowResetReason
from cade.agent.context_policy import (
    ContextPolicy,
    current_durable_ids,
    latest_task_message,
    protected_working_set,
)
from cade.agent.messages import (
    AgentMessage,
    AssistantMessage,
    BranchSummaryMessage,
    SystemMessage,
    ToolResultMessage,
    UserMessage,
)
from cade.agent.types import TextContent, ToolCallContent, ToolInput, ToolSpec

from ..skill_activation import activated_skill_names, is_skill_activation_content

_RESET_TAG = "<context-window-reset"


class ContextWindowController:
    """在工具调用或 UI 与 agent 循环之间传递一次换窗请求。"""

    def __init__(self) -> None:
        self._reason: ContextWindowResetReason | None = None

    def request(self, reason: ContextWindowResetReason) -> bool:
        self._reason = reason
        return True

    def consume(self) -> ContextWindowResetReason | None:
        reason = self._reason
        self._reason = None
        return reason


class ContextWindowRollover:
    """关闭旧窗口，带走持久状态、用户请求与有界的近期完整交互。"""

    def __init__(self) -> None:
        self.last_window_id: str | None = None

    def rollover_messages(
        self,
        messages: list[AgentMessage],
        *,
        preserve_user_request: bool = True,
        policy: ContextPolicy | None = None,
    ) -> list[AgentMessage]:
        """使用同一策略保留原始消息；不再另行按角色或字符数裁剪。"""
        active_policy = policy or ContextPolicy()
        window_id = uuid4().hex[:12]
        self.last_window_id = window_id
        restored: list[AgentMessage] = []
        for message in messages:
            if not isinstance(message, SystemMessage):
                break
            if _RESET_TAG not in message.content:
                restored.append(message.model_copy(deep=True))
        restored.append(SystemMessage(content=render_context_window_reset(window_id)))
        protected_ids = current_durable_ids(messages)
        skills: dict[str, AgentMessage] = {}
        for message in messages:
            content = (
                message.content if not isinstance(message, BranchSummaryMessage) else ""
            )
            if isinstance(content, list):
                content = "\n".join(
                    b.text for b in content if isinstance(b, TextContent)
                )
            for name in activated_skill_names(content):
                skills[name] = UserMessage(content=content)
        restored.extend(skills.values())
        if preserve_user_request:
            latest_user = latest_task_message(messages)
            if latest_user is not None and not is_skill_activation_content(
                latest_user.content
            ):
                restored.append(latest_user.model_copy(deep=True))
        retained_calls = {
            block.id
            for m in restored
            if isinstance(m, AssistantMessage)
            for block in m.content
            if isinstance(block, ToolCallContent)
        }
        # 同一调用组只注入一次；持久工具状态不受普通近期工作集配额淘汰。
        for group in (
            protected_working_set(messages, protected_ids),
            active_policy.recent_working_set(messages, protected_ids)
            if preserve_user_request
            else [],
        ):
            for m in group:
                if isinstance(m, AssistantMessage):
                    calls = {b.id for b in m.content if isinstance(b, ToolCallContent)}
                    if calls and calls.issubset(retained_calls):
                        continue
                    retained_calls.update(calls)
                elif isinstance(m, ToolResultMessage):
                    if any(
                        isinstance(r, ToolResultMessage)
                        and r.tool_call_id == m.tool_call_id
                        for r in restored
                    ):
                        continue
                restored.append(m.model_copy(deep=True))
        actions = _render_completed_actions(messages)
        if actions:
            for position, message in enumerate(restored):
                if isinstance(message, SystemMessage) and message.content.startswith(
                    _RESET_TAG
                ):
                    restored[position] = message.model_copy(
                        update={"content": f"{message.content}\n\n{actions}"}
                    )
                    break
        return restored

    def __call__(
        self,
        messages: list[dict[str, Any]],
        *,
        preserve_user_request: bool = True,
    ) -> list[dict[str, Any]]:
        """仅适配会话展示格式；策略只在类型化消息路径执行。"""
        from .agent_helpers import to_dict
        from .message_codec import messages_from_provider_dicts

        return [
            to_dict(message)
            for message in self.rollover_messages(
                messages_from_provider_dicts(messages),
                preserve_user_request=preserve_user_request,
            )
        ]


def _render_completed_actions(messages: list[AgentMessage]) -> str:
    """为最近已结束的调用保存有界索引，不携带输出或可执行命令。"""
    calls = {
        block.id: block
        for message in messages
        if isinstance(message, AssistantMessage)
        for block in message.content
        if isinstance(block, ToolCallContent)
    }
    lines: list[str] = []
    seen: set[str] = set()
    for message in reversed(messages):
        if not isinstance(message, ToolResultMessage):
            continue
        call = calls.get(message.tool_call_id)
        if call is None or call.id in seen:
            continue
        seen.add(call.id)
        record: dict[str, object] = {
            "tool_call_id": call.id,
            "tool": call.name,
            "status": "error" if message.is_error else "returned",
        }
        args = call.arguments or {}
        path = args.get("path")
        if isinstance(path, str):
            record["path"] = path
        metadata = message.metadata or {}
        code = metadata.get("exit_code")
        if isinstance(code, int) and not isinstance(code, bool):
            record["exit_code"] = code
        line = json.dumps(record, ensure_ascii=False)
        if len(("\n".join([*lines, line])).encode()) > 1536:
            continue
        lines.append(line)
        if len(lines) == 6:
            break
    if not lines:
        return ""
    return (
        "<recent-tool-actions>\n"
        "Raw index of the previous window's latest completed calls, not a claim "
        "that code is correct or the task is complete. Continue the pending action "
        "using NOTE.md and current validation facts. Avoid restarting broad file "
        "discovery just because the tool transcript was released; read exact "
        "history or current files only for a specific missing detail. Search "
        "history by tool_call_id for the original arguments and output.\n"
        + "\n".join(reversed(lines))
        + "\n</recent-tool-actions>"
    )


def has_working_note(project_root: Path) -> bool:
    """检查项目根是否存在可用于干净换窗的工作交接记录。"""
    note_path = project_root / "NOTE.md"
    try:
        return bool(note_path.read_text(encoding="utf-8", errors="replace").strip())
    except OSError:
        return False


def build_new_context_tool(
    controller: ContextWindowController,
    project_root: Path,
) -> tuple[ToolSpec, ...]:
    """构建模型主动换窗工具；笔记用于交接但不阻塞释放窗口。"""

    def request_new_context(
        data: ToolInput,
        _on_update: Callable[[str], None] | None = None,
    ) -> str:
        reason = str(data.get("reason", "")).strip()
        controller.request("model")
        return (
            "Fresh context window scheduled before the next inference. No summary "
            f"will be generated. Reason: {reason}"
        )

    return (
        ToolSpec(
            name="new_context",
            description=(
                "Close the current model context and continue in a fresh window "
                "without generating a summary. Update NOTE.md when possible with the "
                "execution frontier; older exact details remain in history."
            ),
            input_hint='JSON: {"reason":"the active window is stale or noisy"}',
            handler=request_new_context,
            schema={
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Why a fresh working window is useful now.",
                    }
                },
                "required": ["reason"],
                "additionalProperties": False,
            },
            prompt_snippet=(
                "Use new_context when the current working set is stale or near its "
                "token budget. Save progress in NOTE.md before the budget is exhausted; no summary is generated."
            ),
        ),
    )


def render_context_window_reset(window_id: str) -> str:
    """渲染新窗口的最小恢复协议。"""
    return (
        f'<context-window-reset id="{window_id}">\n'
        "The previous context window was closed without a summary. The current "
        "task continues in this window; prior messages remain available in history. "
        "This window change is not a new task or another process restart. "
        "Read NOTE.md when "
        "present for the execution frontier. If it is missing, write a minimal "
        "checkpoint of the goal, constraints, known progress and next action before "
        "more investigation; mark unknown state explicitly rather than recovering "
        "the whole transcript first. Continue the next unfinished action; "
        "reuse verified results unless relevant files changed or evidence is "
        "uncertain. If the task is complete, report completion. "
        "The lossless session transcript is "
        "authoritative; use history list_windows/search/read/around for older "
        "exact details.\n"
        "</context-window-reset>"
    )
