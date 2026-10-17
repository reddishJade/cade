"""read_file 工具注册器与 Schema。"""

from __future__ import annotations

import threading
from pathlib import Path

from cade.agent.types import LocationRenderIntent, ToolInput, ToolOutput, ToolSpec
from cade.harness.execution_env import FileSystem, LocalFileSystem

from .file_image import detect_image, read_image
from .path_utils import (
    SAMPLE_BYTES,
    is_binary_file,
    matches_blocked_pattern,
    resolve_absolute_path,
    truncate_output,
)
from .utils import _display, read_text, safe_path

READ_FILE_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "A relative or absolute file or directory path.",
        },
        "limit": {
            "type": "integer",
            "description": "Optional max number of lines to return.",
        },
        "offset": {
            "type": "integer",
            "description": "Optional 1-based line number to start reading from.",
        },
    },
    "required": ["path"],
    "additionalProperties": False,
}


def build_read_tool(
    project_root: Path,
    operations: FileSystem | None = None,
    cancel_event: threading.Event | None = None,
) -> ToolSpec:
    root = project_root.resolve()
    ops = operations or LocalFileSystem()

    def handler(data, _on_update=None):
        if cancel_event is not None and cancel_event.is_set():
            raise ValueError("Tool cancelled")
        return read_file_content(root, ops, data)

    return ToolSpec(
        name="read",
        description="Read a file or directory from the local filesystem.",
        input_hint='JSON: {"path": "/absolute/path/to/file", "offset": 1, "limit": 80}',
        handler=handler,
        schema=READ_FILE_SCHEMA,
        execution_mode="parallel",
        prompt_guidelines=(
            "Use read offset and limit to continue reading long files.",
        ),
    )


MAX_READ_LIMIT = 2000
MAX_LINE_LENGTH = 2000
MAX_BYTES = 50 * 1024
MAX_BYTES_LABEL = "50 KB"
MAX_LINE_SUFFIX = f"... (line truncated to {MAX_LINE_LENGTH} chars)"
MAX_FILE_READ_BYTES = 1_000_000


def read_file_content(
    root: Path,
    operations: FileSystem,
    data: ToolInput,
) -> str:
    path_str = str(data.get("path", "")).strip()
    if not path_str:
        raise ValueError("path is required")

    filepath = resolve_absolute_path(root, path_str)
    if matches_blocked_pattern(filepath):
        raise ValueError(f"path is blocked: {_display(root, filepath)}")

    if not operations.exists(filepath):
        _file_not_found(filepath, root, operations)

    display = _display(root, filepath)
    if operations.is_dir(filepath):
        return _read_directory(filepath, display, operations, data)

    mime = detect_image(filepath, operations)
    if mime is not None:
        return read_image(filepath, display, mime, operations)

    sample = operations.read_head(filepath, SAMPLE_BYTES)
    if is_binary_file(filepath, sample):
        return ToolOutput(
            f"Cannot read binary file: {display}",
            is_error=True,
        )

    offset = _parse_offset(data)
    limit = _parse_limit(data)
    return _read_text_file(filepath, display, offset, limit, operations)


def _parse_offset(data: ToolInput) -> int:
    raw = data.get("offset")
    if raw is None:
        return 1
    try:
        val = int(raw)
    except (OverflowError, TypeError, ValueError):
        raise ValueError("offset must be an integer") from None
    if val < 1:
        raise ValueError("offset must be positive")
    return val


def _parse_limit(data: ToolInput) -> int:
    raw = data.get("limit")
    if raw is None:
        return MAX_READ_LIMIT
    try:
        val = int(raw)
    except (OverflowError, TypeError, ValueError):
        raise ValueError("limit must be an integer") from None
    if val < 0:
        raise ValueError("limit must be non-negative")
    return val if val > 0 else MAX_READ_LIMIT


def _file_not_found(filepath: Path, root: Path, operations: FileSystem) -> str:
    dir_path = filepath.parent
    base_name = filepath.stem if filepath.suffix else filepath.name
    suggestions: list[str] = []

    try:
        if operations.is_dir(dir_path):
            for entry_name, _ in operations.read_dir_entries(dir_path):
                if (
                    base_name.lower() in entry_name.lower()
                    or entry_name.lower() in base_name.lower()
                ):
                    suggestions.append(str(dir_path / entry_name))
                    if len(suggestions) >= 3:
                        break
    except OSError:
        pass

    display = _display(root, filepath)
    if suggestions:
        raise ValueError(
            f"File not found: {display}\n\n"
            f"Did you mean one of these?\n"
            f"{chr(10).join(suggestions)}"
        )
    raise ValueError(f"File not found: {display}")


def _truncate_line(line: str) -> str:
    if len(line) > MAX_LINE_LENGTH:
        return line[:MAX_LINE_LENGTH] + MAX_LINE_SUFFIX
    return line


def _read_text_file(
    path: Path,
    display: str,
    offset: int,
    limit: int,
    operations: FileSystem,
) -> ToolOutput:
    start = offset - 1
    lines: list[str] = []
    bytes_count = 0
    total_count = 0
    more = False
    cut = False

    for line in operations.iter_lines(path):
        total_count += 1
        if total_count <= start:
            continue
        if len(lines) >= limit:
            more = True
            continue
        truncated = _truncate_line(line)
        line_bytes = len(truncated.encode("utf-8")) + (1 if lines else 0)
        if bytes_count + line_bytes > MAX_BYTES:
            cut = True
            more = True
            break
        lines.append(truncated)
        bytes_count += line_bytes

    if total_count < offset and not (total_count == 0 and offset == 1):
        raise ValueError(
            f"Offset {offset} is out of range for this file ({total_count} lines)"
        )

    last_line = offset + len(lines) - 1
    next_line = offset if not lines else last_line + 1

    output_parts = [
        f"<path>{display}</path>",
        "<type>file</type>",
        "<content>",
    ]
    for i, line in enumerate(lines):
        output_parts.append(f"{i + offset}: {line}")

    if cut:
        output_parts.append(
            f"\n(Output capped at {MAX_BYTES_LABEL}. "
            f"Showing lines {offset}-{last_line}. "
            f"Use offset={next_line} to continue.)"
        )
    elif more:
        output_parts.append(
            f"\n(Showing lines {offset}-{last_line} of {total_count}. "
            f"Use offset={next_line} to continue.)"
        )
    else:
        output_parts.append(f"\n(End of file - total {total_count} lines)")
    output_parts.append("</content>")
    text = "\n".join(output_parts)

    return ToolOutput(
        text,
        metadata={
            "display": {
                "type": "file",
                "path": display,
                "text": "\n".join(lines),
                "lineStart": offset,
                "lineEnd": last_line,
                "totalLines": total_count,
                "truncated": more or cut,
            },
        },
        render_intent=LocationRenderIntent(
            path=display,
            line_start=offset,
            line_end=last_line,
        ),
    )


def _read_directory(
    path: Path,
    display: str,
    operations: FileSystem,
    data: ToolInput,
) -> ToolOutput:
    items = operations.read_dir_entries(path)
    formatted = [name + "/" if is_dir else name for name, is_dir in items]

    offset = data.get("offset", 1)
    limit = data.get("limit", MAX_READ_LIMIT)

    start = offset - 1
    sliced = formatted[start : start + limit]
    truncated = start + len(sliced) < len(formatted)

    output_parts = [
        f"<path>{display}</path>",
        "<type>directory</type>",
        "<entries>",
    ]
    output_parts.extend(sliced)

    if truncated:
        output_parts.append(
            f"\n(Showing {len(sliced)} of {len(formatted)} entries. "
            f"Use 'offset' parameter to read beyond entry {offset + len(sliced)})"
        )
    else:
        output_parts.append(f"\n({len(formatted)} entries)")
    output_parts.append("</entries>")
    text = "\n".join(output_parts)

    return ToolOutput(
        text,
        metadata={
            "display": {
                "type": "directory",
                "path": display,
                "entries": sliced,
                "offset": offset,
                "totalEntries": len(formatted),
                "truncated": truncated,
            },
        },
        render_intent=LocationRenderIntent(path=display),
    )


def read_project_text_file(project_root: Path, raw_path: str) -> str:
    operations = LocalFileSystem()
    root = project_root.resolve()
    path = safe_path(root, raw_path)
    if not operations.is_file(path):
        raise ValueError(f"not a file: {_display(root, path)}")
    size = operations.size(path)
    if size > MAX_FILE_READ_BYTES:
        raise ValueError(f"file too large: {size} bytes")
    text, _encoding = read_text(path, operations)
    return truncate_output(text)
