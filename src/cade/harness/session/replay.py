"""从 append-only session transcript 重建 agent 运行状态。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from cade.agent.messages import AgentMessage

from .store import SessionStore
from .surface import project_session_surface
from .types import SessionEntry


class ReplayAgent(Protocol):
    """会话投影器恢复运行状态所需的 agent 接口。"""

    @property
    def session_id(self) -> str: ...

    @session_id.setter
    def session_id(self, value: str) -> None: ...

    def set_history_session_id(self, session_id: str) -> None: ...

    def load_history(self, messages: list[AgentMessage]) -> None: ...

    def restore_run_state_metadata(self, payload: object) -> None: ...

    def restore_goal_state(self, payload: object) -> None: ...

    def set_resumed_notice(self, notice: str) -> None: ...


def replay_session(
    agent: ReplayAgent,
    store: SessionStore,
    restoration_context_renderer: Callable[[object], str | None] | None = None,
) -> None:
    """把当前 session branch 的事实投影回 agent 内存。"""
    agent.session_id = store.session_id
    agent.set_history_session_id(store.session_id)
    records = store.build_branch()
    surface = project_session_surface(records, artifacts_dir=store.artifacts_dir)
    agent.load_history(list(surface.messages))
    run_state = latest_run_state(records)
    if run_state is not None:
        agent.restore_run_state_metadata(run_state)
    goal_state = latest_goal_state(records)
    if goal_state is not None:
        agent.restore_goal_state(goal_state)
    restoration_notice = None
    if restoration_context_renderer is not None:
        restoration_notice = restoration_context_renderer(
            latest_restoration_context(records)
        )
    if surface.generation > 0:
        notice = (
            "This session resumed in its latest context window. Consult NOTE.md "
            "for explicit working state when present; the lossless transcript is "
            "authoritative. Use history for older exact details instead of asking "
            "the user to restate them."
        )
    else:
        notice = (
            "This conversation was resumed from a previous session. "
            "The transcript history above has been loaded as context. "
            "Continue the task as if the session was uninterrupted."
        )
    if restoration_notice:
        notice = f"{notice}\n\n{restoration_notice}"
    unresolved = unresolved_tool_attempts(records)
    if unresolved:
        notice = (
            f"{notice}\n\nRecovery warning: {len(unresolved)} prior tool "
            "invocation(s) have no recorded result. Their filesystem or external "
            "side effects are UNKNOWN. Inspect current state and the original "
            "session history before repeating those operations; a missing result "
            "does not prove that the action failed."
        )
    agent.set_resumed_notice(notice)


def unresolved_tool_attempts(records: list[SessionEntry]) -> tuple[str, ...]:
    """查找当前日志窗口中无结果的工具调用。

    工具调用在权限审查和执行前就写入日志，只能说明调用发生过；缺少结果时
    无法判断实际结果。换窗事件明确结束前一个模型可见窗口。
    """
    pending: dict[str, str] = {}
    for record in records:
        if record.type != "event" or not isinstance(record.content, dict):
            continue
        event_type = record.content.get("type")
        data = record.content.get("data")
        if event_type == "context_window_reset":
            pending.clear()
        elif event_type == "tool_use" and isinstance(data, dict):
            call_id = data.get("id")
            if isinstance(call_id, str) and call_id:
                pending[call_id] = call_id
        elif event_type == "tool_result" and isinstance(data, dict):
            call_id = data.get("tool_use_id")
            if isinstance(call_id, str):
                pending.pop(call_id, None)
    return tuple(pending)


def latest_run_state(records: list[SessionEntry]) -> object | None:
    """读取当前 branch 最近一次 final event 中的 run_state。"""
    for record in reversed(records):
        if record.type != "event" or not isinstance(record.content, dict):
            continue
        if record.content.get("type") != "final":
            continue
        data = record.content.get("data")
        if not isinstance(data, dict):
            continue
        run_state = data.get("run_state")
        if isinstance(run_state, dict):
            return run_state
    return None


def latest_goal_state(records: list[SessionEntry]) -> object | None:
    """按时间倒序读取 Goal 命令或 final 中的最新状态。"""
    for record in reversed(records):
        if record.type != "event" or not isinstance(record.content, dict):
            continue
        event_type = record.content.get("type")
        data = record.content.get("data")
        if event_type == "goal_state" and isinstance(data, dict):
            return data
        if event_type != "final" or not isinstance(data, dict):
            continue
        run_state = data.get("run_state")
        if isinstance(run_state, dict) and isinstance(run_state.get("goal"), dict):
            return run_state["goal"]
    return None


def latest_restoration_context(records: list[SessionEntry]) -> object | None:
    """读取最近一次换窗时持久化的产品层恢复上下文。"""
    for record in reversed(records):
        if record.type != "event" or not isinstance(record.content, dict):
            continue
        if record.content.get("type") != "context_window_reset":
            continue
        data = record.content.get("data")
        if isinstance(data, dict):
            return data.get("restoration_context")
    return None


def records_to_agent_messages(
    records: list[SessionEntry], *, artifacts_dir: Path | None = None
) -> list[AgentMessage]:
    """将事实账本与工件投影为模型历史，不以展示预览替换正文。"""
    return list(project_session_surface(records, artifacts_dir=artifacts_dir).messages)
