"""Edit 工具：对已有文本文件执行明确的精确替换。"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

from cade.agent.types import DiffRenderIntent, ToolInput, ToolOutput, ToolSpec
from cade.harness.execution_env import FileSystem, LocalFileSystem

from .file_mutation_queue import with_file_mutation
from .text_edit import apply_text_edits, apply_text_replacement, normalize_to_lf
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
            "description": "The path to an existing file (relative or absolute).",
        },
        "edits": {
            "type": "array",
            "minItems": 1,
            "description": "Unique, non-overlapping changes matched against the original file.",
            "items": {
                "type": "object",
                "properties": {
                    "old_text": {"type": "string", "minLength": 1},
                    "new_text": {"type": "string"},
                },
                "required": ["old_text", "new_text"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["path", "edits"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class EditFileRequest:
    path: Path
    edits: tuple[tuple[str, str], ...]
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
            "Each old_text must be unique and edits must not overlap. "
            "Use write for new files or full replacements."
        ),
        input_hint='JSON: {"path": "src/x.py", "edits": [{"old_text": "...", "new_text": "..."}]}',
        handler=handler,
        schema=EDIT_FILE_SCHEMA,
        prompt_guidelines=(
            "Use edit for precise changes to existing files.",
            "All edits match the original file, not earlier edits.",
            "Merge overlapping changes into one edit entry.",
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

    if "edits" in data:
        if "old_text" in data or "new_text" in data or "replace_all" in data:
            raise ValueError("Use edits or legacy old_text/new_text, not both")
        items = data["edits"]
        if not isinstance(items, list) or not items:
            raise ValueError("edits must be a non-empty array")
        edits: list[tuple[str, str]] = []
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                raise TypeError(f"edits[{index}] must be an object")
            old_text = item.get("old_text")
            new_text = item.get("new_text")
            if not isinstance(old_text, str) or not old_text.strip():
                raise ValueError(f"edits[{index}].old_text must not be empty")
            if not isinstance(new_text, str):
                raise TypeError(f"edits[{index}].new_text is required")
            edits.append((old_text, new_text))
        return EditFileRequest(path=path, edits=tuple(edits), replace_all=False)

    old_text = data.get("old_text")
    new_text = data.get("new_text")
    if not isinstance(old_text, str) or not old_text.strip():
        raise ValueError("old_text must not be empty")
    if not isinstance(new_text, str):
        raise TypeError("new_text is required")
    return EditFileRequest(
        path=path,
        edits=((old_text, new_text),),
        replace_all=bool(data.get("replace_all", False)),
    )


def _edit_file_impl(
    root: Path,
    request: EditFileRequest,
    operations: FileSystem,
) -> ToolOutput:
    path = request.path
    before, encoding = read_text(path, operations)
    display = _display(root, path)
    try:
        if request.replace_all:
            old_text, new_text = request.edits[0]
            updated = apply_text_replacement(
                before, old_text, new_text, replace_all=True
            )
            total = normalize_to_lf(before).count(normalize_to_lf(old_text))
        else:
            updated = apply_text_edits(before, request.edits)
            total = len(request.edits)
    except ValueError as exc:
        raise ValueError(f"{exc} in {display}") from exc

    if updated == before:
        raise ValueError(f"No changes made to {display}.")

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
