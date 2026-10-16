"""Write 工具：创建或完整覆盖一个文本文件。"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

from cade.agent.types import ToolInput, ToolOutput, ToolSpec
from cade.harness.execution_env import FileSystem, LocalFileSystem

from .file_mutation_queue import with_file_mutation
from .utils import (
    _display,
    ensure_write_size,
    write_safe_path,
    write_text,
)

WRITE_FILE_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "The path to the file to write (relative or absolute).",
        },
        "content": {
            "type": "string",
            "description": "The content to write to the file.",
        },
    },
    "required": ["path", "content"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class WriteFileRequest:
    path: Path
    content: str


def build_write_tool(
    project_root: Path,
    operations: FileSystem | None = None,
    cancel_event: threading.Event | None = None,
) -> ToolSpec:
    root = project_root.resolve()
    ops = operations or LocalFileSystem()

    def handler(data: ToolInput, _on_update=None) -> ToolOutput:
        if cancel_event is not None and cancel_event.is_set():
            raise ValueError("Tool cancelled")
        return write_file_content(root, ops, data)

    return ToolSpec(
        name="write",
        description=(
            "Create a new text file or intentionally replace an entire file. "
            "Prefer edit for targeted changes to an existing file."
        ),
        input_hint='JSON: {"path": "src/example.py", "content": "..."}',
        handler=handler,
        schema=WRITE_FILE_SCHEMA,
        prompt_guidelines=(
            "Use write only for new files or deliberate full-file rewrites.",
        ),
    )


def write_file_content(
    root: Path,
    operations: FileSystem,
    data: ToolInput,
) -> ToolOutput:
    request = _parse_write_file_request(root, operations, data)
    return with_file_mutation(
        request.path,
        lambda: _write_file_impl(root, request, operations),
    )


def _parse_write_file_request(
    root: Path,
    operations: FileSystem,
    data: ToolInput,
) -> WriteFileRequest:
    path_str = str(data.get("path", "")).strip()
    if not path_str:
        raise ValueError("path is required")
    path = write_safe_path(root, path_str)
    if operations.exists(path) and operations.is_dir(path):
        raise ValueError(f"path is a directory: {_display(root, path)}")
    if "content" not in data:
        raise ValueError("content is required")
    content = str(data.get("content", ""))
    ensure_write_size(content)
    return WriteFileRequest(path=path, content=content)


def _write_file_impl(
    root: Path,
    request: WriteFileRequest,
    operations: FileSystem,
) -> ToolOutput:
    operations.mkdir(request.path.parent)
    display = _display(root, request.path)

    write_text(request.path, request.content, "utf-8", operations)
    return ToolOutput(f"wrote file: {display}")
