"""Experience 的稀疏自动提示：只在确定性触发条件命中时才注入指针。

触发不使用问题、embedding 或 top-k 检索，只用两类已由当前任务显式建立的
事实：本 session 真实读写的文件，以及失败工具输出或用户消息里原样出现的
错误签名。命中后只交付一行指针，正文必须由 Agent 显式 recall。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

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
    Experience,
    Freshness,
    anchor_freshness,
    parse_experience,
    render_hint_line,
)
from .manager import MemoryManager

MEMORY_HINT_LIMIT = 3
MEMORY_HINT_MAX_CHARS = 600
MEMORY_HINT_CANDIDATES = 12
MEMORY_HINT_RECENT_ERRORS = 6

_NOTE = (
    "Historical hints from MEMORY.md, not current facts. The repository is "
    "authoritative; recall the full record and verify the anchor before applying."
)
_OPEN = f'<memory-hints note="{_NOTE}">'
_CLOSE = "</memory-hints>"


@dataclass(frozen=True)
class MemoryHint:
    """一条待注入的 Experience 指针。"""

    experience: Experience
    matched: str
    freshness: Freshness
    matched_path: bool


def select_hints(
    manager: MemoryManager,
    *,
    recent_files: tuple[str, ...],
    recent_text: str,
    limit: int = MEMORY_HINT_LIMIT,
    candidates: int = MEMORY_HINT_CANDIDATES,
) -> tuple[MemoryHint, ...]:
    """按锚点交集挑选提示；路径命中优先于错误签名，且只保留最相关的若干条。"""
    if not recent_files and not recent_text:
        return ()
    matched: list[MemoryHint] = []
    for record in manager.read_memory_records("all"):
        experience = parse_experience(record)
        if experience is None:
            continue
        hit = _match(experience, recent_files, recent_text)
        if hit is None:
            continue
        matched.append(hit)
        if len(matched) >= candidates:
            break
    scored = [
        MemoryHint(
            experience=hint.experience,
            matched=hint.matched,
            freshness=anchor_freshness(hint.experience, manager.root),
            matched_path=hint.matched_path,
        )
        for hint in matched
    ]
    # Python 的排序是稳定的：同分时保持文件顺序，不做额外的时间推断。
    scored.sort(key=_hint_order)
    return tuple(scored[:limit])


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
        limit: int = MEMORY_HINT_LIMIT,
        max_chars: int = MEMORY_HINT_MAX_CHARS,
        recent_errors: int = MEMORY_HINT_RECENT_ERRORS,
    ) -> None:
        self._manager = manager
        self._recent_files = recent_files
        self._limit = limit
        self._max_chars = max_chars
        self._recent_errors = recent_errors

    def collect(self, input: ContextCollectionInput) -> list[ContextBlock]:
        hints = select_hints(
            self._manager,
            recent_files=self._recent_files(),
            recent_text=self._recent_text(input),
            limit=self._limit,
        )
        body, emitted = render_hint_block(hints, max_chars=self._max_chars)
        if not body:
            return []
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
) -> MemoryHint | None:
    for path in reversed(recent_files):
        matched = experience.matches_path(path)
        if matched is not None:
            return MemoryHint(
                experience=experience,
                matched=f"path:{matched}",
                freshness="unknown",
                matched_path=True,
            )
    if not recent_text:
        return None
    error = experience.matches_error(recent_text)
    if error is None:
        return None
    return MemoryHint(
        experience=experience,
        matched=f"error:{error}",
        freshness="unknown",
        matched_path=False,
    )


def _hint_order(hint: MemoryHint) -> tuple[int, int]:
    """路径命中优先，其次锚点未变更的历史提示。"""
    return (
        0 if hint.matched_path else 1,
        0 if hint.freshness == "unchanged" else 1,
    )


def _result_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            block.text for block in content if isinstance(block, TextContent)
        )
    return str(content)
