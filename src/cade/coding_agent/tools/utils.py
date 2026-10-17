"""文件工具复用的路径、文本 I/O 与 Diff 辅助函数。"""

from __future__ import annotations

from difflib import unified_diff
from pathlib import Path

from cade.harness.execution_env import FileSystem

from .path_utils import (
    display_path,
    is_path_blocked,
    matches_blocked_pattern,
    resolve_absolute_path,
    resolve_project_path,
)

MAX_WRITE_BYTES = 1_000_000


def write_safe_path(root: Path, raw_path: str) -> Path:
    path_str = raw_path.strip().strip("\"'")
    if not path_str:
        raise ValueError("path is required")
    filepath = resolve_absolute_path(root, path_str)
    if matches_blocked_pattern(filepath):
        raise ValueError(f"path is blocked: {_display(root, filepath)}")
    return filepath


def safe_path(root: Path, raw_path: str) -> Path:
    path = resolve_project_path(root, raw_path)
    if is_path_blocked(root, path):
        raise ValueError(f"path is blocked: {display_path(root, path)}")
    return path


def read_text(path: Path, operations: FileSystem) -> tuple[str, str]:
    data = operations.read_bytes(path)
    encoding = "utf-8-sig" if data.startswith(b"\xef\xbb\xbf") else "utf-8"
    try:
        return data.decode(encoding), encoding
    except UnicodeDecodeError:
        pass
    raise UnicodeDecodeError("utf-8", data, 0, 1, "file is not valid UTF-8 text")


def write_text(
    path: Path,
    text: str,
    encoding: str,
    operations: FileSystem,
) -> None:
    operations.write_bytes(path, text.encode(encoding))


def ensure_write_size(text: str) -> None:
    size = len(text.encode("utf-8"))
    if size > MAX_WRITE_BYTES:
        raise ValueError(f"write content too large: {size} bytes")


def diff_preview(path: str, before: str, after: str) -> str:
    lines = unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
    )
    return "".join(lines)


def first_changed_line(before: str, after: str) -> int | None:
    before_lines = before.splitlines()
    after_lines = after.splitlines()
    for index, (before_line, after_line) in enumerate(
        zip(before_lines, after_lines, strict=False), start=1
    ):
        if before_line != after_line:
            return index
    if len(before_lines) != len(after_lines):
        return min(len(before_lines), len(after_lines)) + 1
    return None


def _display(root: Path, path: Path) -> str:
    return display_path(root, path)
