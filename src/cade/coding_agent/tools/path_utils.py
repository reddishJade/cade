from __future__ import annotations

from pathlib import Path

import pathspec

from .truncate import truncate_tail

DEFAULT_MAX_LINES = 2000
DEFAULT_MAX_BYTES = 50 * 1024


def resolve_project_path(project_root: Path, raw_path: str) -> Path:
    requested_path = Path(raw_path.strip().strip("\"'") or ".")
    if ".." in requested_path.parts:
        raise ValueError("parent-directory paths are not allowed")
    root = project_root.resolve()
    candidate = (
        requested_path.resolve()
        if requested_path.is_absolute()
        else (root / requested_path).resolve()
    )
    if candidate != root and root not in candidate.parents:
        raise ValueError("path escapes project root")
    return candidate


_BLOCKED_SPEC = pathspec.PathSpec.from_lines(
    "gitignore",
    [
        ".git/",
        ".venv/",
        "__pycache__/",
        ".env",
    ],
)


def is_path_blocked(root: Path, path: Path) -> bool:
    try:
        relative = path.resolve().relative_to(root)
    except ValueError:
        return True
    return _BLOCKED_SPEC.match_file(relative.as_posix())


def truncate_output(
    text: str,
    max_lines: int = DEFAULT_MAX_LINES,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> str:
    return truncate_tail(text, max_lines=max_lines, max_bytes=max_bytes).content


def display_path(root: Path, path: Path) -> str:
    """将绝对路径转为相对于 root 的 POSIX 路径，逃逸时回退为绝对路径。"""
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return str(path)


_BINARY_EXTENSIONS: set[str] = {
    ".zip",
    ".tar",
    ".gz",
    ".exe",
    ".dll",
    ".so",
    ".class",
    ".jar",
    ".war",
    ".7z",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".odt",
    ".ods",
    ".odp",
    ".bin",
    ".dat",
    ".obj",
    ".o",
    ".a",
    ".lib",
    ".wasm",
    ".pyc",
    ".pyo",
}

SAMPLE_BYTES = 4096


def is_binary_file(path: Path, sample: bytes) -> bool:
    if path.suffix.lower() in _BINARY_EXTENSIONS:
        return True
    if not sample:
        return False
    non_printable = 0
    for b in sample:
        if b == 0:
            return True
        if b < 9 or (b > 13 and b < 32):
            non_printable += 1
    return non_printable / len(sample) > 0.3


def resolve_absolute_path(root: Path, raw_path: str) -> Path:
    path_str = raw_path.strip().strip("\"'")
    if not path_str:
        raise ValueError("path is required")
    p = Path(path_str)
    if p.is_absolute():
        resolved = p.resolve()
    else:
        if ".." in p.parts:
            raise ValueError("parent-directory paths are not allowed")
        resolved = (root / p).resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            raise ValueError("path escapes project root") from None
    return resolved


def matches_blocked_pattern(path: Path) -> bool:
    try:
        return _BLOCKED_SPEC.match_file(path.resolve().as_posix())
    except OSError:
        return False
