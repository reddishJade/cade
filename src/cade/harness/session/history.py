"""当前 branch 检索及跨 Session 原始事件的精确读取。"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from cade.agent.types import ToolInput, ToolSpec

from .artifacts import resolve_history_event_content
from .schema import SESSION_EVENT_SCHEMA_VERSION
from .tree_store import build_session_branch, read_session_entries

_SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


@dataclass(frozen=True)
class HistoryEntry:
    """用于召回的一条原始 session 记录。"""

    id: str
    parent_id: str | None
    type: str
    content: object
    created_at: str
    artifacts_dir: Path | None = field(default=None, repr=False, compare=False)

    @property
    def text(self) -> str:
        claimed = _claimed_display_text(self.content)
        if claimed is not None:
            return claimed
        resolved = resolve_history_event_content(self.content, self.artifacts_dir)
        if isinstance(resolved, str):
            return resolved
        return json.dumps(resolved, ensure_ascii=False, sort_keys=True)

    @property
    def search_text(self) -> str:
        """返回不解析外置 artifact 的有界检索文本。"""
        claimed = _claimed_display_text(self.content)
        if claimed is not None:
            return claimed
        if isinstance(self.content, str):
            return self.content
        return json.dumps(self.content, ensure_ascii=False, sort_keys=True)


@dataclass(frozen=True)
class HistoryRead:
    """一条历史记录的可分页原文。"""

    entry: HistoryEntry
    offset: int
    content: str
    total_chars: int

    @property
    def next_offset(self) -> int | None:
        next_offset = self.offset + len(self.content)
        return next_offset if next_offset < self.total_chars else None


@dataclass(frozen=True)
class ContextWindowRecord:
    """session transcript 中一次换窗事件。"""

    window_id: str
    event_entry_id: str
    trigger: str
    created_at: str


class SessionHistory:
    """检索当前 branch，并按稳定来源精确读取 workspace 历史。"""

    def __init__(
        self,
        sessions_dir: Path,
        *,
        project_root: Path,
        artifacts_dir: Path | None = None,
    ) -> None:
        self.sessions_dir = sessions_dir
        self.artifacts_dir = artifacts_dir or _default_artifacts_dir(sessions_dir)
        self.session_id: str | None = None
        self.project_root = project_root.resolve()

    def get_entry(self, session_id: str, entry_id: str) -> HistoryEntry | None:
        """按稳定来源定位 entry，不受当前 head 或会话切换影响。"""
        return next(
            (entry for entry in self._entries(session_id) if entry.id == entry_id),
            None,
        )

    def set_session_id(self, session_id: str) -> None:
        if not _SESSION_ID.fullmatch(session_id):
            raise ValueError(f"invalid session id: {session_id!r}")
        self.session_id = session_id

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        include_artifacts: bool = False,
        include_derived: bool = False,
    ) -> list[HistoryEntry]:
        """按关键词相关性搜索当前 branch。"""
        terms = _tokens(query)
        if not terms:
            return []
        phrase = query.strip().casefold()
        scored: list[tuple[int, int, HistoryEntry]] = []
        branch = self._branch()
        retrieval_calls = _retrieval_call_ids(branch)
        for index, entry in enumerate(branch):
            if not include_derived and not _primary_search_entry(
                entry, retrieval_calls
            ):
                continue
            text = (entry.text if include_artifacts else entry.search_text).casefold()
            matches = sum(min(text.count(term), 3) for term in terms)
            if matches == 0:
                continue
            phrase_bonus = 10 if phrase and phrase in text else 0
            scored.append(
                (matches + phrase_bonus + _entry_priority(entry), index, entry)
            )
        scored.sort(key=lambda item: (-item[0], -item[1]))
        return [entry for _, _, entry in scored[: min(max(limit, 1), 20)]]

    def around(
        self,
        entry_id: str,
        *,
        before: int = 3,
        after: int | None = None,
        session_id: str | None = None,
    ) -> list[HistoryEntry]:
        """返回指定记录附近的 branch 原文。"""
        if session_id is not None and after is not None:
            raise ValueError(
                "after is only supported on the current branch; omit session_id"
            )
        if session_id is None:
            branch = self._branch()
        else:
            by_id = {entry.id: entry for entry in self._entries(session_id)}
            branch = []
            current = by_id.get(entry_id)
            while current is not None:
                branch.append(current)
                current = by_id.get(current.parent_id or "")
            branch.reverse()
        index = next(
            (position for position, entry in enumerate(branch) if entry.id == entry_id),
            None,
        )
        if index is None:
            return []
        start = max(0, index - min(max(before, 0), 20))
        end = min(
            len(branch), index + min(max(after if after is not None else 3, 0), 20) + 1
        )
        return branch[start:end]

    def read(
        self,
        entry_id: str,
        *,
        offset: int = 0,
        max_chars: int = 8_000,
        session_id: str | None = None,
    ) -> HistoryRead | None:
        """按字符范围读取一条记录，不使用预览截断。"""
        entry = (
            self.get_entry(session_id, entry_id)
            if session_id is not None
            else next(
                (entry for entry in self._branch() if entry.id == entry_id),
                None,
            )
        )
        if entry is None:
            return None
        text = entry.text
        safe_offset = min(max(offset, 0), len(text))
        safe_limit = min(max(max_chars, 1), 20_000)
        return HistoryRead(
            entry=entry,
            offset=safe_offset,
            content=text[safe_offset : safe_offset + safe_limit],
            total_chars=len(text),
        )

    def list_windows(self, *, limit: int = 20) -> list[ContextWindowRecord]:
        """列出当前 branch 上已持久化的上下文窗口边界。"""
        windows: list[ContextWindowRecord] = []
        for entry in self._branch():
            if entry.type != "event" or not isinstance(entry.content, dict):
                continue
            if entry.content.get("type") != "context_window_reset":
                continue
            data = entry.content.get("data")
            if not isinstance(data, dict):
                continue
            window_id = str(data.get("window_id", "")).strip()
            if not window_id:
                continue
            windows.append(
                ContextWindowRecord(
                    window_id=window_id,
                    event_entry_id=entry.id,
                    trigger=str(data.get("trigger", "")),
                    created_at=entry.created_at,
                )
            )
        return windows[-min(max(limit, 1), 100) :]

    def _branch(self) -> list[HistoryEntry]:
        session_id = self.session_id
        if session_id is None:
            return []
        path = self.sessions_dir / f"session-{session_id}.jsonl"
        entries = read_session_entries(path)
        legacy_head = (
            self._head_id(session_id)
            if entries and entries[-1].head_id is None
            else None
        )
        branch = build_session_branch(entries, legacy_head)
        return [
            HistoryEntry(
                id=entry.id,
                parent_id=entry.parent_id,
                type=entry.type,
                content=entry.content,
                created_at=entry.created_at,
                artifacts_dir=self.artifacts_dir,
            )
            for entry in branch
        ]

    def _entries(self, session_id: str) -> list[HistoryEntry]:
        """只读取许可目录中的指定会话，并检查 workspace 所属关系。"""
        if not _SESSION_ID.fullmatch(session_id):
            raise ValueError(f"invalid session id: {session_id!r}")
        sessions_root = self.sessions_dir.resolve()
        path = self.sessions_dir / f"session-{session_id}.jsonl"
        if path.is_symlink() or not path.resolve().is_relative_to(sessions_root):
            raise ValueError("History session path escapes its store")
        entries = read_session_entries(path)
        if not entries:
            return []
        if sessions_root != self.project_root / ".cade" / "sessions":
            owner = entries[0].project_path
            if owner is None:
                raise ValueError(
                    "Cannot verify workspace ownership of this external session"
                )
            if (
                not Path(owner).is_absolute()
                or Path(owner).resolve() != self.project_root
            ):
                raise ValueError("History session does not belong to this workspace")
        return [
            HistoryEntry(
                id=entry.id,
                parent_id=entry.parent_id,
                type=entry.type,
                content=entry.content,
                created_at=entry.created_at,
                artifacts_dir=self.artifacts_dir,
            )
            for entry in entries
        ]

    def _head_id(self, session_id: str) -> str | None:
        index_dir = (
            self.sessions_dir.parent
            if self.sessions_dir.name == "sessions"
            else self.sessions_dir
        )
        path = index_dir / "session_index.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        sessions = payload.get("sessions", []) if isinstance(payload, dict) else []
        for item in sessions:
            if isinstance(item, dict) and item.get("id") == session_id:
                value = item.get("head_id")
                return str(value) if value else None
        return None


def build_history_tools(history: SessionHistory) -> tuple[ToolSpec, ...]:
    """构建当前 session 的窗口索引、搜索和精确读取工具。"""

    def handle(
        data: ToolInput,
        _on_update: Callable[[str], None] | None = None,
    ) -> str:
        operation = str(data.get("operation", "search"))
        session_id = data.get("session_id")
        if session_id is not None and (
            not isinstance(session_id, str) or not session_id
        ):
            raise ValueError("session_id must be a nonempty string")
        if session_id is not None and operation not in {"read", "around"}:
            raise ValueError("session_id is only supported for exact read/around")
        if operation == "list_windows":
            windows = history.list_windows(limit=_bounded(data.get("limit"), 20, 100))
            if not windows:
                return "No context window transitions in the current session."
            return "\n".join(_render_window(window) for window in windows)
        if operation == "search":
            query = str(data.get("query", "")).strip()
            if not query:
                return "query is required for history search"
            entries = history.search(
                query,
                limit=_bounded(data.get("limit"), 5, 20),
                include_artifacts=data.get("include_artifacts") is True,
                include_derived=data.get("include_derived") is True,
            )
        elif operation == "read":
            entry_id = str(data.get("entry_id", "")).strip()
            if not entry_id:
                return "entry_id is required for history read"
            result = history.read(
                entry_id,
                offset=_bounded(data.get("offset"), 0, 1_000_000_000),
                max_chars=_bounded(data.get("max_chars"), 8_000, 20_000),
                session_id=session_id,
            )
            if result is None:
                return "No matching history entry."
            return (
                f"[session_id={session_id or history.session_id}]\n"
                + _render_exact_read(result)
            )
        elif operation == "around":
            entry_id = str(data.get("entry_id", "")).strip()
            if not entry_id:
                return "entry_id is required for history around"
            entries = history.around(
                entry_id,
                before=_bounded(data.get("before"), 3, 20),
                after=_bounded(data["after"], 3, 20) if "after" in data else None,
                session_id=session_id,
            )
        else:
            return "operation must be one of: list_windows, search, read, around"
        if not entries:
            return "No matching history entry."
        return f"[session_id={session_id or history.session_id}]\n" + "\n\n".join(
            _render_entry(
                entry, query=str(data.get("query", "")) if operation == "search" else ""
            )
            for entry in entries
        )

    return (
        ToolSpec(
            name="history",
            description=(
                "List context windows, search the current session's lossless "
                "transcript, page through one exact record, or inspect neighbors. "
                "Supply session_id for an exact read in another workspace-local session, "
                "including abandoned branches; around then follows only the anchor "
                "ancestry, with no descendants. This never switches the session/head. "
                "Search returns bounded excerpts and match offsets when an exact term "
                "occurs in a large record; use those offsets for targeted reads."
                " Default search excludes retrieval echoes, runtime reminders and "
                "audit/reset copies; include_derived=true searches all records."
            ),
            input_hint=(
                'JSON: {"operation":"list_windows"}, '
                '{"operation":"search","query":"timeout","limit":5}, '
                '{"operation":"read","entry_id":"abc123","offset":0,'
                '"max_chars":8000}, or {"operation":"around",'
                '"entry_id":"abc123","before":3,"after":3}'
            ),
            handler=handle,
            schema={
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": ["list_windows", "search", "read", "around"],
                    },
                    "query": {"type": "string"},
                    "entry_id": {"type": "string"},
                    "session_id": {
                        "type": "string",
                        "description": "Exact read/around only; omitted means current branch.",
                    },
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                    "include_artifacts": {"type": "boolean"},
                    "include_derived": {"type": "boolean"},
                    "before": {"type": "integer", "minimum": 0, "maximum": 20},
                    "after": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 20,
                        "description": "Current branch only; omit with session_id.",
                    },
                    "offset": {"type": "integer", "minimum": 0},
                    "max_chars": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 20000,
                    },
                },
                "required": ["operation"],
                "additionalProperties": False,
            },
            prompt_snippet=(
                "After a context reset, use list_windows/search to locate evidence, "
                "then read for exact content. If a page is marked omitted from the request, "
                "reduce max_chars and retry the same offset; next_offset describes "
                "the stored page, not the admitted preview. History is the source of truth."
            ),
        ),
    )


def _default_artifacts_dir(sessions_dir: Path) -> Path:
    if sessions_dir.name == "sessions":
        return sessions_dir.parent / "session_artifacts"
    return sessions_dir / "session_artifacts"


def _render_entry(entry: HistoryEntry, *, query: str = "") -> str:
    content = entry.text
    match = re.search(re.escape(query), content, re.IGNORECASE) if query else None
    if match is not None and len(content) > 2000:
        offset = max(0, match.start() - 400)
        excerpt = content[offset : offset + 1200]
        return (
            f"[entry_id={entry.id} type={entry.type} at={entry.created_at} "
            f"match_offset={match.start()} excerpt_offset={offset}]\n{excerpt}"
        )
    if len(content) > 2000:
        content = (
            content[:1200]
            + f"\n[…{len(content) - 2000} chars omitted…]\n"
            + content[-800:]
        )
    return f"[entry_id={entry.id} type={entry.type} at={entry.created_at}]\n{content}"


def _render_exact_read(result: HistoryRead) -> str:
    next_offset = str(result.next_offset) if result.next_offset is not None else "end"
    return (
        f"[entry_id={result.entry.id} type={result.entry.type} "
        f"at={result.entry.created_at} offset={result.offset} "
        f"total_chars={result.total_chars} next_offset={next_offset}]\n"
        f"{result.content}"
    )


def _render_window(window: ContextWindowRecord) -> str:
    return (
        f"[window_id={window.window_id} event_entry_id={window.event_entry_id} "
        f"trigger={window.trigger} at={window.created_at}]"
    )


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(re.findall(r"[a-z0-9_./:-]+|[\u3400-\u9fff]", text.casefold()))
    )


def _retrieval_call_ids(branch: list[HistoryEntry]) -> set[str]:
    """先识别检索调用，再过滤其回显；不能依赖事件落盘顺序。"""
    calls: set[str] = set()
    for entry in branch:
        content = entry.content
        if not isinstance(content, dict) or content.get("type") != "tool_use":
            continue
        data = content.get("data")
        if isinstance(data, dict) and data.get("name") == "history":
            calls.add(str(data.get("id", "")))
    return calls


def _primary_search_entry(entry: HistoryEntry, retrieval_calls: set[str]) -> bool:
    """搜索默认只召回原始对话和工具事实，派生复制可显式查询。"""
    if entry.type in {"user", "assistant"}:
        return True
    content = entry.content
    if entry.type != "event" or not isinstance(content, dict):
        return False
    kind = content.get("type")
    data = content.get("data")
    if kind == "inbox/claimed" and isinstance(data, dict):
        messages = data.get("message")
        text = _claimed_display_text(content) or ""
        return (
            isinstance(messages, list)
            and any(isinstance(m, dict) and m.get("kind") == "user" for m in messages)
            and not text.lstrip().startswith(("<reminder>", "<plan-timeout>"))
        )
    if kind == "tool_result" and isinstance(data, dict):
        return str(data.get("tool_use_id", "")) not in retrieval_calls
    if kind == "tool_use" and isinstance(data, dict):
        return data.get("name") != "history"
    if kind == "assistant" and isinstance(data, list):
        return any(
            isinstance(block, dict)
            and (
                (
                    block.get("type") == "text"
                    and bool(str(block.get("text", "")).strip())
                )
                or (block.get("type") == "tool_use" and block.get("name") != "history")
            )
            for block in data
        )
    return kind == "final"


def _entry_priority(entry: HistoryEntry) -> int:
    if _claimed_display_text(entry.content) is not None:
        return 20
    if entry.type in {"user", "assistant"}:
        return 8
    return 0


def _bounded(value: object, default: int, maximum: int) -> int:
    if not isinstance(value, (str, int, float)):
        return default
    try:
        return min(max(int(value), 0), maximum)
    except (OverflowError, ValueError):
        return default


def _claimed_display_text(content: object) -> str | None:
    """从 inbox claim 提取适合召回和展示的用户原文。"""
    if not isinstance(content, dict) or content.get("type") != "inbox/claimed":
        return None
    if content.get("schema_version") != SESSION_EVENT_SCHEMA_VERSION:
        return None
    data = content.get("data")
    if not isinstance(data, dict):
        return None
    display_text = data.get("display_text")
    return display_text if isinstance(display_text, str) else None
