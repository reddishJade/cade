from __future__ import annotations

import shlex
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import cast

from cade.agent.messages import AgentMessage
from cade.agent.types import TextContent, ToolInput
from cade.coding_agent.execution_modes import ExecutionMode
from cade.harness.session.types import JsonValue, SessionEntry, SessionInfoView
from cade.harness.snapshot import TurnSnapshotRecord

from .commands import (
    COMMAND_GROUP_AUTH,
    COMMAND_GROUP_EXIT,
    COMMAND_GROUP_INFO,
    COMMAND_GROUP_MODE,
    COMMAND_GROUP_MODEL,
    COMMAND_GROUP_SESSION_BRANCH,
    COMMAND_GROUP_SESSION_LIFECYCLE,
    COMMAND_GROUP_SESSION_ROLLBACK,
    Choice,
    CommandContext,
    CommandEntry,
    command_names,
    generate_help_text,
)
from .context_stats import _format_token, compute_context_summary
from .sessions import current_view, resume_latest, resumed_message
from .skills import activate_skill
from .tools import run_tool_command


def _queue_followup(ctx: CommandContext, text: str) -> None:
    """把斜杠命令产生的模型输入写入 durable inbox。"""
    from cade.agent.messages import UserMessage

    ctx.app.agent.followup(UserMessage(content=text), display_text=text)


def cmd_help(cmd: str, ctx: CommandContext) -> bool:
    """打印帮助信息。"""
    ctx.output.write(HELP_TEXT)
    return False


def cmd_clear(cmd: str, ctx: CommandContext) -> bool:
    """清空当前会话记录并开始新会话。"""
    ctx.store.clear()
    ctx.app.restore_session()
    return False


def cmd_fork(cmd: str, ctx: CommandContext) -> bool:
    """从某条 user 消息截断，新建会话。"""
    msgs = ctx.store.get_forkable_user_messages()
    if not msgs:
        ctx.output.write("No user messages to fork from.")
        return False

    def _fork_title(e: SessionEntry) -> str:
        if isinstance(e.content, dict):
            data = e.content.get("data")
            if isinstance(data, dict):
                return str(data.get("display_text", ""))
        return ""

    choices = [
        Choice(
            title=" ".join(_fork_title(e).split())[:100],
            value=e,
        )
        for e in msgs
    ]
    selected = ctx.output.select("Select message to fork from:", choices=choices)
    if selected is None:
        return False

    parent_session_id = ctx.store.session_id
    forked = ctx.store.fork_from_entry(selected.id)
    ctx.store.current_path = forked.current_path
    meta = ctx.store.current_metadata()
    if ctx.snapshot_store is not None and meta is not None:
        ctx.snapshot_store.fork_session(parent_session_id, meta.id)
    ctx.app.restore_session()
    ctx.output.write(f'Forked at: "{meta.title if meta else selected.id[:8]}"')
    return False


def cmd_clone(cmd: str, ctx: CommandContext) -> bool:
    """完整复制当前会话到新文件。"""
    parent_session_id = ctx.store.session_id
    cloned = ctx.store.fork_into()
    fork_meta = cloned.current_metadata()
    if ctx.snapshot_store is not None and fork_meta is not None:
        ctx.snapshot_store.fork_session(parent_session_id, fork_meta.id)
    ctx.store.current_path = cloned.current_path
    ctx.app.restore_session()
    if fork_meta is not None:
        ctx.output.write(f'Cloned: "{fork_meta.title}" ({fork_meta.id})')
    return False


def cmd_rewind(cmd: str, ctx: CommandContext) -> bool:
    """回退最近的 N 轮用户交互。"""
    try:
        turns = _parse_turn_count(cmd)
    except ValueError as exc:
        ctx.output.write(str(exc))
        return False
    removed = ctx.store.rewind_turns(turns)
    if ctx.snapshot_store is not None:
        ctx.snapshot_store.rewind_to_turn_count(
            ctx.store.session_id,
            ctx.store.user_turn_count(),
        )
    ctx.app.restore_session()
    turn_label = "turn" if turns == 1 else "turns"
    ctx.output.write(
        f"Rewound {turns} user {turn_label} ({removed} transcript records removed)."
    )
    return False


def cmd_resume(cmd: str, ctx: CommandContext) -> bool:
    """从最近的或指定的会话恢复。"""
    parts = cmd.split(maxsplit=1)
    if len(parts) == 2:
        target = parts[1].strip()
        if target == "last":
            view = resume_latest(ctx.store)
            if view:
                _print_resumed_session(view, ctx)
                ctx.app.restore_session()
            else:
                ctx.output.write("No conversations found.")
            return False
        ctx.store.resume(target)
        _print_resumed_session(current_view(ctx.store), ctx)
        ctx.app.restore_session()
        return False
    selected = _select_session(ctx, ctx.store.list_infos())
    if selected is not None:
        ctx.store.resume(selected.id)
        _print_resumed_session(selected, ctx)
    ctx.app.restore_session()
    return False


def cmd_tree(cmd: str, ctx: CommandContext) -> bool:
    """显示会话分支树，选中的 entry 设为当前位置。"""
    nodes = ctx.store.get_tree()
    if not nodes:
        ctx.output.write("No session tree available (no metadata).")
        return False

    choices = [
        Choice(
            title=f"{'  ' * n.depth}{'└─ ' if n.depth > 0 else ''}{n.title}{' ← current' if n.is_current else ''}",
            value=n,
        )
        for n in nodes
    ]
    selected = ctx.output.select("Jump to entry:", choices=choices)
    if selected is None:
        return False

    if not ctx.store.jump_to_entry(selected.id):
        ctx.output.write("Failed to set entry.")
        return False

    ctx.app.restore_session()
    ctx.output.write(f"Moved to: {selected.title}")
    return False


def cmd_continue(cmd: str, ctx: CommandContext) -> bool:
    """切换到当前项目最新的有意义会话。"""
    view = ctx.store.find_latest_for_project(ctx.project_root)
    if view is None:
        ctx.output.write("No prior session found for this project.")
        return False
    if view.id == ctx.store.session_id:
        ctx.output.write(f"Already on the latest session: {view.title}")
        return False
    ctx.store.resume(view.id)
    _print_resumed_session(view, ctx)
    ctx.app.restore_session()
    return False


def cmd_sessions(cmd: str, ctx: CommandContext) -> bool:
    """交互式选择并恢复历史会话。"""
    sessions = ctx.store.list_infos()
    if not sessions:
        ctx.output.write("No conversations found.")
        return False

    selected = _select_session(ctx, sessions)
    if selected is None:
        return False

    ctx.store.resume(selected.id)
    ctx.app.restore_session()
    _print_resumed_session(selected, ctx)
    return False


def _select_session(
    ctx: CommandContext, sessions: list[SessionInfoView]
) -> SessionInfoView | None:
    return ctx.output.select(
        "Select session to resume:",
        [Choice(f"{item.title} - {item.summary}", item) for item in sessions],
    )


def _print_resumed_session(view: SessionInfoView, ctx: CommandContext) -> None:
    ctx.output.write(resumed_message(view))


def cmd_rename(cmd: str, ctx: CommandContext) -> bool:
    """重命名当前会话。"""
    parts = cmd.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        current = ctx.store.current_metadata()
        if current:
            ctx.output.write(f'Current title: "{current.title}"')
        ctx.output.write("Usage: /rename <title>")
        return False
    new_title = parts[1].strip()
    meta = ctx.store.rename_session(new_title)
    if meta is None:
        ctx.output.write("No active session to rename.")
        return False
    ctx.output.write(f'Session renamed to: "{meta.title}"')
    return False


def cmd_mode(cmd: str, ctx: CommandContext) -> bool:
    """列出执行模式或切换到指定模式。"""
    parts = cmd.split(maxsplit=1)
    selected = parts[1].strip().lower() if len(parts) == 2 else None
    if selected is None:
        selected = ctx.output.select(
            "Select execution mode:",
            choices=[Choice(mode, mode) for mode in ("act", "build", "plan")],
            default=ctx.state.mode,
        )
    if selected is None:
        return False
    if selected not in {"act", "build", "plan"}:
        ctx.output.write("Usage: /mode <act|build|plan>")
        return False
    ctx.state.mode = cast(ExecutionMode, selected)
    ctx.app.agent.set_mode(ctx.state.mode)
    return False


def cmd_steer(cmd: str, ctx: CommandContext) -> bool:
    """向当前运行的 agent 注入实时指导，下次推理前生效。"""
    parts = cmd.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        ctx.output.write("Usage: /steer <message>")
        return False
    msg = parts[1].strip()
    from cade.agent.messages import UserMessage

    outcome = ctx.app.agent.steer(UserMessage(content=msg))
    if not outcome.wake_required:
        ctx.output.write("[steer] injected into the active run")
    else:
        ctx.output.write("[steer] queued for the next run")
    return False


def cmd_queue(cmd: str, ctx: CommandContext) -> bool:
    """设置忙时策略，或把消息加入 next-run follow-up 队列。"""
    parts = cmd.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        ctx.output.write(f"Current busy-message mode: {ctx.state.busy_mode.value}")
        ctx.output.write("Usage: /queue steer|followup|interrupt|<message>")
        return False
    msg = parts[1].strip()
    from cade.harness.agent_runtime import BusyMessageMode

    if msg in {mode.value for mode in BusyMessageMode}:
        ctx.state.busy_mode = BusyMessageMode(msg)
        ctx.output.write(f"Busy-message mode set to {msg}.")
        return False

    from cade.agent.messages import UserMessage

    ctx.app.agent.followup(UserMessage(content=msg))
    ctx.output.write("[queued] will start a new run after the current run finishes")
    return False


def _replace_context_window(
    ctx: CommandContext,
    *,
    preserve_user_request: bool,
    action: str,
) -> bool:
    """立即执行无摘要硬换窗并持久化新的 surface。"""
    from cade.agent._context_window import estimate_message_tokens
    from cade.harness.agent_runtime.agent_helpers import to_dict
    from cade.harness.agent_runtime.message_codec import (
        messages_from_provider_dicts,
    )

    agent = getattr(ctx.app, "agent", None)
    if agent is None:
        ctx.output.write("No agent available.")
        return False

    # 1) 获取 agent 当前消息
    history_messages = getattr(agent, "history_messages", None)
    if not callable(history_messages):
        ctx.output.write("Agent does not expose history.")
        return False
    before_msgs = cast(Callable[[], list[AgentMessage]], history_messages)()
    if not before_msgs:
        ctx.output.write("No active context to replace.")
        return False

    before_tokens = estimate_message_tokens(before_msgs)

    rollover = getattr(agent, "context_rollover", None)
    if not callable(rollover):
        ctx.output.write("Context-window rollover is not configured.")
        return False

    load_history = getattr(agent, "load_history", None)
    if not callable(load_history):
        ctx.output.write("Agent does not support history replacement.")
        return False

    from cade.harness.agent_runtime.context_window import ContextWindowRollover

    if isinstance(rollover, ContextWindowRollover):
        after_msgs = rollover.rollover_messages(
            before_msgs, preserve_user_request=preserve_user_request
        )
    else:
        dict_messages = [to_dict(message) for message in before_msgs]
        next_window = cast(Callable[..., list[dict[str, object]]], rollover)(
            dict_messages, preserve_user_request=preserve_user_request
        )
        after_msgs = messages_from_provider_dicts(next_window)
    after_tokens = estimate_message_tokens(after_msgs)

    cast(Callable[[list[AgentMessage]], None], load_history)(after_msgs)

    window_id = str(getattr(rollover, "last_window_id", "") or "")
    if not window_id:
        raise RuntimeError("context rollover did not produce a window id")
    ctx.app.record_context_window_reset(
        window_id=window_id,
        messages_before=len(before_msgs),
        messages_after=len(after_msgs),
        replacement=after_msgs,
    )

    retention = (
        "Retained the latest user request."
        if preserve_user_request
        else "Previous turns remain available through history."
    )
    ctx.output.write(
        f"{action} {window_id}: {len(before_msgs)} messages \u2192 "
        f"{len(after_msgs)} messages ({before_tokens:,} \u2192 "
        f"{after_tokens:,} estimated tokens). {retention} "
        "No summary was generated."
    )
    return False


def cmd_compact(cmd: str, ctx: CommandContext) -> bool:
    """通过硬换窗压缩上下文，并保留最近一个用户请求。"""
    return _replace_context_window(
        ctx,
        preserve_user_request=True,
        action="Compacted into fresh context",
    )


def cmd_rollover(cmd: str, ctx: CommandContext) -> bool:
    """丢弃普通对话投影并开启干净窗口。"""
    parts = cmd.split()
    force = len(parts) == 2 and parts[1] == "--force"
    if len(parts) > 2 or (len(parts) == 2 and not force):
        ctx.output.write("Usage: /rollover [--force]")
        return False

    from cade.harness.agent_runtime.context_window import has_working_note

    if not force and not has_working_note(ctx.project_root):
        ctx.output.write(
            "Context rollover not started. Write NOTE.md with the current goal, "
            "confirmed decisions, verification status, unresolved issues, and "
            "next action; then run /rollover again. Use /rollover --force to "
            "continue without a working note."
        )
        return False

    return _replace_context_window(
        ctx,
        preserve_user_request=False,
        action="Rolled over to fresh context",
    )


def cmd_goal(cmd: str, ctx: CommandContext) -> bool:
    """设置、显示、暂停、恢复或清除当前 session 的停止条件。"""
    parts = cmd.split(maxsplit=1)
    condition = parts[1].strip() if len(parts) == 2 else ""
    agent = ctx.app.agent
    if not condition:
        active = agent.goal_condition
        if active is None:
            ctx.output.write("No active goal.")
        else:
            status = "paused" if agent.goal_paused else "active"
            ctx.output.write(f"Goal: {active} [{status}]")
        return False
    action = condition.lower()
    if action in {"clear", "reset"}:
        agent.clear_goal()
        _persist_goal_state(ctx)
        ctx.output.write("Goal cleared.")
        return False
    if action == "pause":
        active = agent.goal_condition
        if active is None:
            ctx.output.write("No active goal to pause.")
        elif agent.goal_paused:
            ctx.output.write(f"Goal already paused: {active}")
        else:
            agent.pause_goal()
            _persist_goal_state(ctx)
            ctx.output.write(f"Goal paused: {active}")
        return False
    if action == "resume":
        active = agent.goal_condition
        if active is None:
            ctx.output.write("No paused goal to resume.")
        elif not agent.goal_paused:
            ctx.output.write(f"Goal already active: {active}")
        else:
            agent.resume_goal()
            _persist_goal_state(ctx)
            _queue_followup(
                ctx,
                "Continue working toward the active goal:\n\n" + active,
            )
            ctx.output.write(f"Goal resumed: {active}")
        return False
    agent.set_goal(condition)
    _persist_goal_state(ctx)
    _queue_followup(ctx, condition)
    ctx.output.write(f"Goal set: {condition}")
    return False


def _persist_goal_state(ctx: CommandContext) -> None:
    """把斜杠命令产生的 Goal 状态立即写入当前 session。"""
    goal_state: dict[str, JsonValue] = {
        key: value for key, value in ctx.app.agent.goal_state.items()
    }
    ctx.store.append(
        "event",
        {
            "type": "goal_state",
            "data": goal_state,
        },
    )


def cmd_hooks(cmd: str, ctx: CommandContext) -> bool:
    """显示外部命令 hook 配置来源和最近运行状态。"""
    diagnostics = ctx.app.hook_diagnostics()
    if not diagnostics:
        ctx.output.write("No external hooks configured.")
        return False

    ctx.output.write(f"External hooks ({len(diagnostics)}):")
    for diagnostic in diagnostics:
        matcher = diagnostic.matcher or "*"
        status = (
            f"{diagnostic.last_status} at {diagnostic.last_run_at}"
            if diagnostic.last_run_at
            else diagnostic.last_status
        )
        ctx.output.write(
            f"  [{diagnostic.index}] {diagnostic.event} "
            f"{'enabled' if diagnostic.enabled else 'disabled'} "
            f"matcher={matcher} policy={diagnostic.failure_policy} "
            f"subagents={'yes' if diagnostic.inherit_to_subagents else 'no'}"
        )
        ctx.output.write(
            f"      source={diagnostic.source} runs={diagnostic.run_count} last={status}"
        )
        if diagnostic.last_error:
            ctx.output.write(f"      error={diagnostic.last_error}")
    return False


def cmd_mcp(cmd: str, ctx: CommandContext) -> bool:
    """显示 MCP 状态或手动重载配置。"""
    parts = cmd.split(maxsplit=1)
    action = parts[1].strip() if len(parts) == 2 else "status"
    if action == "reload":
        reload_mcp = getattr(ctx.app, "reload_mcp", None)
        if reload_mcp is None:
            ctx.output.write("MCP runtime is not available.")
            return False
        names = reload_mcp()
        ctx.output.write(f"Reloaded MCP config. Registered {len(names)} MCP tools.")
        return False
    if action != "status":
        ctx.output.write("Usage: /mcp status|reload")
        return False
    mcp_status = getattr(ctx.app, "mcp_status", None)
    if mcp_status is None:
        ctx.output.write("MCP runtime is not available.")
        return False
    statuses = mcp_status()
    if not statuses:
        ctx.output.write("No MCP servers configured.")
        return False
    for status in statuses:
        identity = status.get("server_info") or {}
        identity_text = ""
        if isinstance(identity, dict) and identity:
            name = identity.get("name", "?")
            version = identity.get("version", "?")
            identity_text = f" identity={name}@{version}"
        protocol = status.get("protocol_version")
        protocol_text = f" protocol={protocol}" if protocol else ""
        error = status.get("last_error")
        error_text = f" error={error}" if error else ""
        ctx.output.write(
            f"{status['server_name']}: state={status['state']} "
            f"tools={status['tool_count']} deferred={status['deferred']}"
            f"{protocol_text}{identity_text}{error_text}"
        )
    return False


def cmd_tool(cmd: str, ctx: CommandContext) -> bool:
    """直接执行一个已注册的工具。"""
    output = run_tool_command(cmd, ctx.app)
    ctx.store.append("event", {"type": "tool_command", "data": cmd})
    ctx.store.append("event", {"type": "tool_result", "data": output})
    ctx.output.render(output)
    return False


def cmd_skill(cmd: str, ctx: CommandContext) -> bool:
    """显式激活一个已发现的技能。"""
    parts = cmd.split(maxsplit=2)
    if len(parts) < 2 or not parts[1].strip():
        ctx.output.write("Usage: /skill NAME [prompt]")
        return False
    result = activate_skill(ctx.app, ctx.store, parts[1].strip(), mode=ctx.state.mode)
    ctx.output.write(result.message)
    if result.status in {"activated", "already_active"} and len(parts) == 3:
        prompt = parts[2].strip()
        if prompt:
            _queue_followup(ctx, prompt)
    return False


def cmd_memory(cmd: str, ctx: CommandContext) -> bool:
    """显示本地 Memory 目录，不扫描或载入文件。"""
    ctx.output.write(f"Memory: {ctx.project_root.resolve() / '.cade' / 'memory'}")
    ctx.output.write(
        "Use ordinary read/search or your editor; save_memory records explicit sources."
    )
    return False


def cmd_exit(cmd: str, ctx: CommandContext) -> bool:
    """退出交互界面。"""
    return True


def cmd_context(cmd: str, ctx: CommandContext) -> bool:
    """显示当前会话上下文使用情况，按分类展示 token 用量。"""
    agent = getattr(ctx.app, "agent", None)
    if agent is None:
        ctx.output.write("No agent available.")
        return False

    summary = compute_context_summary(agent, ctx.project_root)

    cost_str = f" · ${summary.spent:.2f}" if summary.spent > 0 else ""
    usage_str = f" · {summary.usage_stats}" if summary.usage_stats else ""
    ctx.output.write(
        f" Context Usage · {summary.model_name} · "
        f"{summary.context_usage}{usage_str}{cost_str}"
    )
    ctx.output.write()

    ICONS = {
        "System prompt": "\u26c1",
        "System tools": "\u26c1",
        "User messages": "\u25c9",
        "Agent responses": "\u25c9",
        "Tool calls": "\u25c9",
        "Skills": "\u26c1",
        "Memory files": "\u26c1",
    }
    for name, tokens in summary.categories:
        icon = ICONS.get(name, " ")
        pct = (
            (tokens / summary.context_window * 100) if summary.context_window > 0 else 0
        )
        ctx.output.write(
            f"   {icon} {name:<18} {_format_token(tokens):>7} tokens ({pct:.1f}%)"
        )

    if summary.context_window > 0:
        free_pct = summary.free / summary.context_window * 100
        ctx.output.write(
            f"   □ {'Free space':<18} {_format_token(summary.free):>7} ({free_pct:.1f}%)"
        )

    if summary.instruction_files:
        ctx.output.write("\n Instructions \u00b7 auto-loaded")
        for f in summary.instruction_files:
            ctx.output.write(f" \u2514 {f}")

    if summary.skill_count > 0:
        skill_token = next((t for n, t in summary.categories if n == "Skills"), 0)
        ctx.output.write("\n Skills \u00b7 /skills")
        ctx.output.write(
            f" \u2514 {summary.skill_count} skills"
            f" \u00b7 {_format_token(skill_token)} tokens"
        )
        if summary.skill_source_dirs:
            seen_labels: set[str] = set()
            for label, path in summary.skill_source_dirs:
                if label not in seen_labels:
                    seen_labels.add(label)
                    ctx.output.write(f"    {label}: {path}")

    return False


def cmd_btw(cmd: str, ctx: CommandContext) -> bool:
    """Ask a quick side question without interrupting the main conversation."""
    parts = cmd.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        ctx.output.write("Usage: /btw <question>")
        return False

    question = parts[1].strip()

    from cade.harness.agent_runtime.events import TextDeltaStructuredEvent

    ctx.output.write("[side question]")

    for event in ctx.app.ask_stream(question, mode=ctx.state.mode):
        if isinstance(event, TextDeltaStructuredEvent):
            ctx.output.write(event.data, end="")

    ctx.output.write()

    ctx.app.restore_session()

    return False


def _parse_turn_count(cmd: str) -> int:
    """在执行回退操作前校验完整的正整数参数。"""
    parts = cmd.split()
    usage = f"Usage: {parts[0]} [positive integer]"
    if len(parts) == 1:
        return 1
    if len(parts) != 2 or not parts[1].isascii() or not parts[1].isdigit():
        raise ValueError(usage)
    count = int(parts[1])
    if count < 1:
        raise ValueError(usage)
    return count


def cmd_undo(cmd: str, ctx: CommandContext) -> bool:
    """回退最近 N 轮用户轮次的文件变更（基于快照恢复）。"""
    if ctx.snapshot_store is None:
        ctx.output.write(
            "Snapshot undo requires a git repository. This project is not a git repo."
        )
        return False

    parts = cmd.split()
    if len(parts) == 2 and parts[1] == "--list":
        records = ctx.snapshot_store.list_records(ctx.store.session_id)
        if not records:
            ctx.output.write("No snapshot records found.")
        else:
            ctx.output.write(f"Snapshot records ({len(records)} total):")
            for r in reversed(records):
                status = "UNDONE" if r.undone else "active"
                ctx.output.write(
                    f"  turn {r.turn_id} [{status}]: "
                    f"{len(r.changed_files)} files, "
                    f"{len(r.skipped_files)} skipped"
                )
        return False

    try:
        n = _parse_turn_count(cmd)
    except ValueError as exc:
        ctx.output.write(str(exc))
        return False
    records = ctx.snapshot_store.get_undoable_records(ctx.store.session_id, n)
    if not records:
        if ctx.snapshot_store.list_records(ctx.store.session_id):
            ctx.output.write("Nothing to undo (all turns already undone).")
        else:
            ctx.output.write("Nothing to undo (no snapshot records).")
        return False

    for record in reversed(records):
        result = _revert_turn(ctx, record)
        _report_undo_result(ctx, record, result)
        if result.fatal_error:
            ctx.output.write("Fatal error during undo. Stack preserved.")
            return False
        if result.skipped:
            ctx.output.write(
                f"Turn {record.turn_id}: undo incomplete; record remains active."
            )
            continue
        record.undone = True
        ctx.snapshot_store.update_record(ctx.store.session_id, record)
    return False


@dataclass
class _RevertResult:
    restored: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    fatal_error: bool = False


def _revert_turn(
    ctx: CommandContext,
    record: TurnSnapshotRecord,
) -> _RevertResult:
    store = ctx.snapshot_store
    if store is None:
        raise RuntimeError("snapshot_store required for undo")
    svc = store.service(ctx.store.session_id)
    result = _RevertResult()

    for entry in record.changed_files:
        try:
            svc._validate_path(entry.path)
            # 部分撤销重试时，已恢复的文件也计入完成结果。
            if not svc.has_conflict(record.pre_snapshot_id, entry.path):
                result.restored.append(entry.path)
                continue
            if svc.has_conflict(record.post_snapshot_id, entry.path):
                result.skipped.append((entry.path, "conflict: file changed after turn"))
                continue

            tool_name = "bash" if entry.kind == "created" else "write"
            original = next(
                (
                    tool
                    for tool in ctx.app.agent.enabled_tools
                    if tool.name == tool_name
                ),
                None,
            )
            if original is None:
                result.skipped.append((entry.path, f"tool unavailable: {tool_name}"))
                continue
            if entry.kind == "created":
                arguments: ToolInput = {
                    "command": f"rm -- {shlex.quote(str(ctx.project_root / entry.path))}"
                }
            else:
                arguments = {
                    "path": entry.path,
                    "content": svc.file_content(record.pre_snapshot_id, entry.path),
                }

            def restore(
                tool_input: ToolInput,
                _on_update: Callable[[str], None] | None,
                *,
                path: str = entry.path,
                kind: str = entry.kind,
                expected: ToolInput = arguments,
            ) -> str:
                if tool_input != expected:
                    raise ValueError(
                        "snapshot restoration requires its original arguments"
                    )
                if svc.has_conflict(record.post_snapshot_id, path):
                    raise ValueError("conflict: file changed during approval")
                if kind == "created":
                    (ctx.project_root / path).unlink()
                else:
                    svc.restore_file(record.pre_snapshot_id, path)
                return f"Restored: {path}"

            execution = ctx.app.agent.execute_tool(
                replace(original, handler=restore), arguments
            )
            if execution.is_error:
                reason = " ".join(
                    block.text
                    for block in execution.content
                    if isinstance(block, TextContent)
                )
                result.skipped.append((entry.path, reason))
            else:
                result.restored.append(entry.path)

        except (ValueError, OSError, subprocess.CalledProcessError) as e:
            result.skipped.append((entry.path, str(e)))
            continue

    return result


def _report_undo_result(
    ctx: CommandContext, record: TurnSnapshotRecord, result: _RevertResult
) -> None:
    if result.fatal_error:
        ctx.output.write(f"Turn {record.turn_id}: fatal error, stack preserved.")
        return
    if result.restored:
        ctx.output.write(
            f"Turn {record.turn_id}: reverted {len(result.restored)} file(s):"
        )
        for p in result.restored:
            ctx.output.write(f"  restored: {p}")
    if result.skipped:
        ctx.output.write(
            f"Turn {record.turn_id}: {len(result.skipped)} file(s) skipped:"
        )
        for path, reason in result.skipped:
            ctx.output.write(f"  skipped: {path} ({reason})")


def cmd_login(cmd: str, ctx: CommandContext) -> bool:
    return ctx.host_command(cmd, ctx)


def cmd_logout(cmd: str, ctx: CommandContext) -> bool:
    return ctx.host_command(cmd, ctx)


def cmd_auth(cmd: str, ctx: CommandContext) -> bool:
    return ctx.host_command(cmd, ctx)


def cmd_model(cmd: str, ctx: CommandContext) -> bool:
    return ctx.host_command(cmd, ctx)


def cmd_effort(cmd: str, ctx: CommandContext) -> bool:
    return ctx.host_command(cmd, ctx)


def cmd_thinking(cmd: str, ctx: CommandContext) -> bool:
    return ctx.host_command(cmd, ctx)


def cmd_config(cmd: str, ctx: CommandContext) -> bool:
    return ctx.host_command(cmd, ctx)


def cmd_permissions(cmd: str, ctx: CommandContext) -> bool:
    return ctx.host_command(cmd, ctx)


COMMAND_REGISTRY: dict[str, CommandEntry] = {
    "/help": CommandEntry(
        handler=cmd_help, desc="Show this help.", group=COMMAND_GROUP_INFO
    ),
    "/clear": CommandEntry(
        handler=cmd_clear,
        desc="Start a new session transcript.",
        group=COMMAND_GROUP_SESSION_LIFECYCLE,
    ),
    "/continue": CommandEntry(
        handler=cmd_continue,
        desc="Resume the latest session for this project.",
        group=COMMAND_GROUP_SESSION_LIFECYCLE,
    ),
    "/new": CommandEntry(
        handler=cmd_clear,
        desc="Start a new session transcript.",
        group=COMMAND_GROUP_SESSION_LIFECYCLE,
    ),
    "/fork": CommandEntry(
        handler=cmd_fork,
        desc="Fork from a user message into a new session.",
        group=COMMAND_GROUP_SESSION_BRANCH,
    ),
    "/clone": CommandEntry(
        handler=cmd_clone,
        desc="Clone current session into a new file.",
        group=COMMAND_GROUP_SESSION_BRANCH,
    ),
    "/rewind": CommandEntry(
        handler=cmd_rewind,
        desc="Remove the last N user turns from the transcript.",
        args_desc="N",
        accepts_args=True,
        group=COMMAND_GROUP_SESSION_ROLLBACK,
    ),
    "/resume": CommandEntry(
        handler=cmd_resume,
        desc="Choose a recent conversation to resume.",
        accepts_args=True,
        group=COMMAND_GROUP_SESSION_LIFECYCLE,
    ),
    "/sessions": CommandEntry(
        handler=cmd_sessions,
        desc="List and resume recent conversations.",
        group=COMMAND_GROUP_SESSION_LIFECYCLE,
    ),
    "/tree": CommandEntry(
        handler=cmd_tree,
        desc="Show session fork tree.",
        group=COMMAND_GROUP_SESSION_BRANCH,
    ),
    "/model": CommandEntry(
        handler=cmd_model,
        desc="Show current model info.",
        args_desc="[profile/]name[:thinking] [--thinking <level>]",
        accepts_args=True,
        group=COMMAND_GROUP_MODEL,
    ),
    "/effort": CommandEntry(
        handler=cmd_effort,
        desc="Select or set reasoning effort.",
        args_desc="[off|none|minimal|low|medium|high|xhigh|max]",
        accepts_args=True,
        group=COMMAND_GROUP_MODEL,
    ),
    "/thinking": CommandEntry(
        handler=cmd_thinking,
        desc="Toggle Responses/Codex reasoning summaries (on/off).",
        args_desc="on|off",
        accepts_args=True,
        group=COMMAND_GROUP_MODEL,
    ),
    "/login": CommandEntry(
        handler=cmd_login,
        desc="Connect an account or API-key provider.",
        args_desc="[provider|account|api_key] [--device]",
        accepts_args=True,
        group=COMMAND_GROUP_AUTH,
    ),
    "/connect": CommandEntry(
        handler=cmd_login,
        desc="Connect an account or API-key provider.",
        args_desc="[provider|account|api_key] [--device]",
        accepts_args=True,
        group=COMMAND_GROUP_AUTH,
    ),
    "/logout": CommandEntry(
        handler=cmd_logout,
        desc="Log out from an AI provider account and clear credentials.",
        args_desc="[provider]",
        accepts_args=True,
        group=COMMAND_GROUP_AUTH,
    ),
    "/auth": CommandEntry(
        handler=cmd_auth,
        desc="Show authentication status or manage accounts.",
        args_desc="status|login|logout",
        accepts_args=True,
        group=COMMAND_GROUP_AUTH,
    ),
    "/config": CommandEntry(
        handler=cmd_config,
        desc="Open the interactive settings browser for cade.config.json.",
        args_desc="[setting]",
        accepts_args=True,
        group=COMMAND_GROUP_INFO,
    ),
    "/mode": CommandEntry(
        handler=cmd_mode,
        desc="Select execution mode (Shift+Tab to cycle).",
        args_desc="[act|build|plan]",
        accepts_args=True,
        group=COMMAND_GROUP_MODE,
    ),
    "/steer": CommandEntry(
        handler=cmd_steer,
        desc="Inject real-time guidance into the active run (next inference).",
        args_desc="<message>",
        accepts_args=True,
        group=COMMAND_GROUP_MODE,
    ),
    "/queue": CommandEntry(
        handler=cmd_queue,
        desc="Set the busy-message mode or enqueue a next-run message.",
        args_desc="steer|followup|collect|interrupt|<message>",
        accepts_args=True,
        group=COMMAND_GROUP_MODE,
    ),
    "/compact": CommandEntry(
        handler=cmd_compact,
        desc="Compact into a fresh window while retaining the latest turn.",
        group=COMMAND_GROUP_SESSION_ROLLBACK,
    ),
    "/rollover": CommandEntry(
        handler=cmd_rollover,
        desc="Start a clean context window using NOTE.md as the handoff.",
        args_desc="[--force]",
        accepts_args=True,
        group=COMMAND_GROUP_SESSION_ROLLBACK,
    ),
    "/goal": CommandEntry(
        handler=cmd_goal,
        desc="Set, pause, resume, or clear an independently verified goal.",
        args_desc="<condition>|pause|resume|clear",
        accepts_args=True,
        group=COMMAND_GROUP_MODE,
    ),
    "/permissions": CommandEntry(
        handler=cmd_permissions,
        desc="List or clear active permission rules and grants.",
        accepts_args=True,
        group=COMMAND_GROUP_INFO,
    ),
    "/hooks": CommandEntry(
        handler=cmd_hooks,
        desc="Show external hook sources and recent status.",
        group=COMMAND_GROUP_INFO,
    ),
    "/mcp": CommandEntry(
        handler=cmd_mcp,
        desc="Show MCP server status or reload .cade/mcp_config.json.",
        args_desc="status|reload",
        accepts_args=True,
        group=COMMAND_GROUP_INFO,
    ),
    "/tool": CommandEntry(
        handler=cmd_tool,
        desc="Run one registered tool directly, or list tools.",
        args_desc="NAME INPUT|list",
        accepts_args=True,
        group=COMMAND_GROUP_INFO,
    ),
    "/skill": CommandEntry(
        handler=cmd_skill,
        desc="Activate a discovered skill for this session.",
        args_desc="NAME",
        accepts_args=True,
        group=COMMAND_GROUP_INFO,
    ),
    "/memory": CommandEntry(
        handler=cmd_memory,
        desc="Show the local Memory directory.",
        group=COMMAND_GROUP_INFO,
    ),
    "/rename": CommandEntry(
        handler=cmd_rename,
        desc="Rename the current session.",
        args_desc="<title>",
        accepts_args=True,
        group=COMMAND_GROUP_SESSION_LIFECYCLE,
    ),
    "/undo": CommandEntry(
        handler=cmd_undo,
        desc="Undo file changes from the last N user turns (via snapshot restore).",
        args_desc="[N|--list]",
        accepts_args=True,
        group=COMMAND_GROUP_SESSION_ROLLBACK,
    ),
    "/exit": CommandEntry(
        handler=cmd_exit, desc="Exit the interface.", group=COMMAND_GROUP_EXIT
    ),
    "/context": CommandEntry(
        handler=cmd_context,
        desc="Show context usage (token count, messages, etc.).",
        group=COMMAND_GROUP_INFO,
    ),
    "/btw": CommandEntry(
        handler=cmd_btw,
        desc="Ask a quick side question without interrupting the main conversation.",
        args_desc="<question>",
        accepts_args=True,
        group=COMMAND_GROUP_INFO,
    ),
    "/quit": CommandEntry(
        handler=cmd_exit,
        desc="Alias for /exit.",
        visible=False,
        group=COMMAND_GROUP_EXIT,
        canonical="/exit",
    ),
    "/revert": CommandEntry(
        handler=cmd_undo,
        desc="Alias for /undo.",
        args_desc="[N|--list]",
        accepts_args=True,
        visible=False,
        group=COMMAND_GROUP_SESSION_ROLLBACK,
        canonical="/undo",
    ),
    "/new-context": CommandEntry(
        handler=cmd_rollover,
        desc="Deprecated alias for /rollover.",
        args_desc="[--force]",
        accepts_args=True,
        visible=False,
        group=COMMAND_GROUP_SESSION_ROLLBACK,
        canonical="/rollover",
    ),
}

COMMAND_NAMES = command_names(COMMAND_REGISTRY)
HELP_TEXT = generate_help_text(COMMAND_REGISTRY)


def handle_command(command: str, ctx: CommandContext) -> bool:
    """按命令注册表执行操作，输入与输出由宿主提供。"""
    for prefix in sorted(COMMAND_REGISTRY, key=len, reverse=True):
        entry = COMMAND_REGISTRY[prefix]
        if command == prefix or (
            entry.accepts_args and command.startswith(prefix + " ")
        ):
            if entry.canonical is not None:
                canonical_entry = COMMAND_REGISTRY[entry.canonical]
                command = entry.canonical + command[len(prefix) :]
                ctx.output.write(command)
                return canonical_entry.handler(command, ctx)
            return entry.handler(command, ctx)
    ctx.output.write(f"Unknown command: {command}")
    return False
