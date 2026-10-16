"""Edit 工具：对已有文本文件执行明确的精确替换。"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

from cade.agent.types import DiffRenderIntent, ToolInput, ToolOutput, ToolSpec
from cade.harness.execution_env import FileSystem, LocalFileSystem

from .file_mutation_queue import with_file_mutation
from .text_edit import apply_text_replacement, normalize_to_lf
from .utils import (
    _display,
    diff_preview,
    ensure_write_size,
    first_changed_line,
    read_text,
    write_safe_path,
    write_text,
)

EDIT_FILE_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "The path to the file to modify (relative or absolute).",
        },
        "old_text": {
            "type": "string",
            "description": "The text to replace (must not be empty).",
        },
        "new_text": {
            "type": "string",
            "description": "Replacement text; must differ from old_text.",
        },
        "replace_all": {
            "type": "boolean",
            "description": "Replace all occurrences of old_text (default false).",
        },
    },
    "required": ["path", "old_text", "new_text"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class EditFileRequest:
    path: Path
    old_text: str
    new_text: str
    replace_all: bool


def build_edit_tool(
    project_root: Path,
    operations: FileSystem | None = None,
    cancel_event: threading.Event | None = None,
) -> ToolSpec:
    root = project_root.resolve()
    ops = operations or LocalFileSystem()

    def handler(data: ToolInput, _on_update=None) -> ToolOutput:
        if cancel_event is not None and cancel_event.is_set():
            raise ValueError("Tool cancelled")
        return edit_file_content(root, ops, data)

    return ToolSpec(
        name="edit",
        description=(
            "Modify an existing text file with a targeted replacement. "
            "old_text matches literally, with LF/CRLF compatibility. "
            "Use write for new files or full replacements."
        ),
        input_hint='JSON: {"path": "src/x.py", "old_text": "...", "new_text": "..."}',
        handler=handler,
        schema=EDIT_FILE_SCHEMA,
        prompt_guidelines=(
            "Use edit for precise changes to existing files.",
            "Provide enough old_text context to ensure a unique match.",
        ),
    )


def edit_file_content(
    root: Path,
    operations: FileSystem,
    data: ToolInput,
) -> ToolOutput:
    request = _parse_edit_file_request(root, operations, data)
    return with_file_mutation(
        request.path,
        lambda: _edit_file_impl(root, request, operations),
    )


def _parse_edit_file_request(
    root: Path,
    operations: FileSystem,
    data: ToolInput,
) -> EditFileRequest:
    path_str = str(data.get("path", "")).strip()
    if not path_str:
        raise ValueError("path is required")
    path = write_safe_path(root, path_str)
    if operations.exists(path) and operations.is_dir(path):
        raise ValueError(f"path is a directory: {_display(root, path)}")
    if not operations.is_file(path):
        raise ValueError(f"not a file: {_display(root, path)}")

    old_text = str(data.get("old_text", ""))
    if not old_text.strip():
        raise ValueError("old_text must not be empty")
    if "new_text" not in data:
        raise ValueError("new_text is required")

    return EditFileRequest(
        path=path,
        old_text=old_text,
        new_text=str(data.get("new_text", "")),
        replace_all=bool(data.get("replace_all", False)),
    )


def _edit_file_impl(
    root: Path,
    request: EditFileRequest,
    operations: FileSystem,
) -> ToolOutput:
    path = request.path
    before, encoding = read_text(path, operations)
    old_text = request.old_text
    new_text = request.new_text
    display = _display(root, path)

    try:
        updated = apply_text_replacement(
            before, old_text, new_text, replace_all=request.replace_all
        )
    except ValueError as exc:
        raise ValueError(f"{exc} in {display}") from exc

    if updated == before:
        raise ValueError(f"No changes made to {display}.")

    total = (
        normalize_to_lf(before).count(normalize_to_lf(old_text))
        if request.replace_all
        else 1
    )
    ensure_write_size(updated)
    write_text(path, updated, encoding, operations)

    patch = diff_preview(display, before, updated)
    first_line = first_changed_line(before, updated)
    return ToolOutput(
        f"edited file: {display} replacements={total}\n{patch}",
        metadata={"patch": patch, "first_changed_line": first_line},
        render_intent=DiffRenderIntent(
            patch=patch, files=(display,), first_changed_line=first_line
        ),
    )
