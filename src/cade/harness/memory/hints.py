"""Experience 的稀疏自动提示：确定性触发、每个窗口只提示一次。

触发只用两类已由当前任务显式建立的事实：本 session 真实读写的文件，以及失败
工具输出或用户消息里原样出现的错误签名。命中后只交付一行指针，正文必须由
Agent 显式 `recall`。

提示状态（`MemoryHintState`）只存在于运行时：换窗即重置，Agent 显式读过某条
经验后也不再自动提示它，绝不写回 MEMORY.md。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from cade.agent._context_window import context_window_id
from cade.agent.context import (
    ContextBlock,
    ContextBlockSource,
    ContextBlockTarget,
    ContextCollectionInput,
    ContextPriority,
)
from cade.agent.context_policy import latest_task_message
from cade.agent.messages import ToolResultMessage
from cade.agent.types import TextContent

from .experience import (
    Anchor,
    Experience,
    Freshness,
    anchor_freshness,
    experience_status,
    parse_experience,
    render_hint_line,
)
from .manager import MemoryManager

MEMORY_HINT_LIMIT = 3
MEMORY_HINT_WINDOW_LIMIT = 5
MEMORY_HINT_MAX_CHARS = 600
MEMORY_HINT_RECENT_ERRORS = 6

_NOTE = (
    "Historical hints from MEMORY.md, not current facts. The repository is "
    "authoritative; recall memory_id for the full record and verify before applying."
)
_OPEN = f'<memory-hints note="{_NOTE}">'
_CLOSE = "</memory-hints>"

_MATCH_RANK = {"file": 0, "dir": 1, "error": 2}
_FRESHNESS_RANK: dict[Freshness, int] = {
    "unchanged": 0,
    "changed": 1,
    "missing": 2,
    "unknown": 3,
}


class MemoryHintState:
    """运行时提示去重状态：换窗重置，显式 recall 后不再自动提示。"""

    def __init__(self) -> None:
        self._window: str | None = None
        self._surfaced: set[str] = set()
        self._emitted = 0

    def begin_window(self, window: str) -> None:
        """进入新的上下文窗口时清空去重状态。"""
        if self._window == window:
            return
        self._window = window
        self._surfaced.clear()
        self._emitted = 0

    def mark_surfaced(self, memory_id: str) -> None:
        """记录一条经验已经交付给模型（自动提示或显式 recall 都算）。"""
        if memory_id:
            self._surfaced.add(memory_id)

    def is_surfaced(self, memory_id: str) -> bool:
        return memory_id in self._surfaced

    @property
    def emitted(self) -> int:
        return self._emitted

    def count_emitted(self, amount: int) -> None:
        self._emitted += amount


@dataclass(frozen=True)
class MemoryHint:
    """一条待注入的 Experience 指针。"""

    experience: Experience
    matched: Anchor
    freshness: Freshness


def select_hints(
    manager: MemoryManager,
    *,
    recent_files: tuple[str, ...],
    recent_text: str,
    state: MemoryHintState | None = None,
    limit: int = MEMORY_HINT_LIMIT,
    window_left: int | None = None,
) -> tuple[MemoryHint, ...]:
    """按锚点交集挑选提示：一次遍历全部记录，再按匹配精度与新鲜度排序。

    不设"先取前 N 条再排序"的候选上限：宽锚点抢先出现时，精确锚点仍必须
    有机会参与竞争。
    """
    if not recent_files and not recent_text:
        return ()
    matched: list[MemoryHint] = []
    for record in manager.read_memory_records("all"):
        status = experience_status(record)
        if status.tier not in {"event", "claim"}:
            continue
        experience = parse_experience(record)
        if experience is None:  # pragma: no cover - status 已判定为合法
            continue
        if state is not None and state.is_surfaced(experience.memory_id):
            continue
        if not experience.auto_hint_anchors:
            continue
        hit = _match(experience, recent_files, recent_text)
        if hit is None:
            continue
        matched.append(
            MemoryHint(
                experience=experience,
                matched=hit,
                freshness=anchor_freshness(experience, manager.root),
            )
        )
    # Python 的排序是稳定的：同分时保持文件顺序，不引入加权评分。
    matched.sort(key=_hint_order)
    budget = limit if window_left is None else max(0, min(limit, window_left))
    return tuple(matched[:budget])


def render_hint_block(
    hints: tuple[MemoryHint, ...],
    *,
    max_chars: int,
) -> tuple[str, int]:
    """渲染有界的提示块；放不下的行整行丢弃，返回 (块, 实际输出条数)。"""
    if not hints:
        return "", 0
    kept: list[str] = []
    for hint in hints:
        line = render_hint_line(hint.experience, hint.freshness)
        if len("\n".join([_OPEN, *kept, line, _CLOSE])) > max_chars:
            break
        kept.append(line)
    if not kept:
        return "", 0
    return "\n".join([_OPEN, *kept, _CLOSE]), len(kept)


class MemoryHintCollector:
    """把锚点命中的 Experience 渲染为低优先级 USER_CONTEXT 提示块。"""

    def __init__(
        self,
        manager: MemoryManager,
        recent_files: Callable[[], tuple[str, ...]],
        *,
        state: MemoryHintState | None = None,
        limit: int = MEMORY_HINT_LIMIT,
        window_limit: int = MEMORY_HINT_WINDOW_LIMIT,
        max_chars: int = MEMORY_HINT_MAX_CHARS,
        recent_errors: int = MEMORY_HINT_RECENT_ERRORS,
    ) -> None:
        self._manager = manager
        self._recent_files = recent_files
        self._state = state or MemoryHintState()
        self._limit = limit
        self._window_limit = window_limit
        self._max_chars = max_chars
        self._recent_errors = recent_errors

    def collect(self, input: ContextCollectionInput) -> list[ContextBlock]:
        self._state.begin_window(context_window_id(input.messages))
        window_left = max(0, self._window_limit - self._state.emitted)
        if window_left <= 0:
            return []
        hints = select_hints(
            self._manager,
            recent_files=self._recent_files(),
            recent_text=self._recent_text(input),
            state=self._state,
            limit=self._limit,
            window_left=window_left,
        )
        body, emitted = render_hint_block(hints, max_chars=self._max_chars)
        if not body:
            return []
        for hint in hints[:emitted]:
            self._state.mark_surfaced(hint.experience.memory_id)
        self._state.count_emitted(emitted)
        return [
            ContextBlock(
                source=ContextBlockSource.MEMORY,
                target=ContextBlockTarget.USER_CONTEXT,
                priority=ContextPriority.LOW,
                content=body,
                provenance=str(self._manager.memory_file),
                truncated=emitted < len(hints),
                truncation_reason="hint_budget" if emitted < len(hints) else None,
                scope="runtime",
            )
        ]

    def _recent_text(self, input: ContextCollectionInput) -> str:
        """只扫描真实失败输出与用户消息，避免把成功输出当作错误证据。"""
        texts: list[str] = []
        latest_user = latest_task_message(input.messages)
        if latest_user is not None and isinstance(latest_user.content, str):
            texts.append(latest_user.content)
        for message in reversed(input.messages):
            if len(texts) > self._recent_errors:
                break
            if not isinstance(message, ToolResultMessage) or not message.is_error:
                continue
            texts.append(_result_text(message.content))
        return "\n".join(texts)


def _match(
    experience: Experience,
    recent_files: tuple[str, ...],
    recent_text: str,
) -> Anchor | None:
    """返回最精确的命中锚点：文件 > 目录 > 错误签名。"""
    file_hit: Anchor | None = None
    dir_hit: Anchor | None = None
    for path in reversed(recent_files):
        matched = experience.matches_path(path)
        if matched is None:
            continue
        if matched.kind == "file":
            return matched
        dir_hit = dir_hit or matched
    file_hit = file_hit or dir_hit
    if file_hit is not None:
        return file_hit
    if not recent_text:
        return None
    return experience.matches_error(recent_text)


def _hint_order(hint: MemoryHint) -> tuple[int, int]:
    """精确匹配优先，其次锚点未变更的历史提示；不加权、不调参。"""
    return (
        _MATCH_RANK[hint.matched.kind],
        _FRESHNESS_RANK[hint.freshness],
    )


def _result_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            block.text for block in content if isinstance(block, TextContent)
        )
    return str(content)
