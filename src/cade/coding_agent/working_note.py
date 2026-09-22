"""NOTE.md 的 session 换窗快照与恢复提示。"""

from __future__ import annotations

from pathlib import Path

from cade.agent.context import NOTES_MAX_BYTES, NOTES_MAX_FILE_BYTES

_TRUNCATED_MARKER = (
    "\n<working-note-truncated>Use history to read the complete snapshot."
    "</working-note-truncated>"
)


def capture_working_note(project_root: Path) -> dict[str, object] | None:
    """捕获可由 NotesCollector 注入的工作笔记。"""
    content = read_working_note(project_root)
    if content is None:
        return None
    return {"kind": "working_note", "content": content}


def render_working_note_restoration(
    project_root: Path,
    restoration_context: object,
) -> str | None:
    """仅在当前文件无法代表旧 session 时注入换窗快照。"""
    if not isinstance(restoration_context, dict):
        return None
    if restoration_context.get("kind") != "working_note":
        return None
    snapshot = restoration_context.get("content")
    if not isinstance(snapshot, str) or not snapshot.strip():
        return None
    current = read_working_note(project_root)
    if current == snapshot:
        return None
    status = "missing" if current is None else "changed"
    budgeted = _apply_byte_budget(snapshot, NOTES_MAX_BYTES)
    return (
        f'<resumed-working-note current_note_status="{status}">\n'
        "This is the working-note snapshot captured at the latest context "
        "transition in this session. The current project NOTE.md is missing or "
        "different; reconcile it with the current worktree and session history "
        "before acting.\n\n"
        f"{budgeted}\n"
        "</resumed-working-note>"
    )


def read_working_note(project_root: Path) -> str | None:
    """按 NotesCollector 的文件上限读取非空工作笔记。"""
    path = project_root / "NOTE.md"
    try:
        if not path.is_file() or path.stat().st_size > NOTES_MAX_FILE_BYTES:
            return None
        content = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    return content or None


def _apply_byte_budget(content: str, max_bytes: int) -> str:
    encoded = content.encode("utf-8")
    if len(encoded) <= max_bytes:
        return content
    marker = _TRUNCATED_MARKER.encode("utf-8")
    available = max(max_bytes - len(marker), 0)
    prefix = encoded[:available].decode("utf-8", errors="ignore")
    return prefix + _TRUNCATED_MARKER
