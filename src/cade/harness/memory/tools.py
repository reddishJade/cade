"""保存可编辑 Markdown，校验显式来源，不解释经验内容。"""

from __future__ import annotations

import os
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path

from filelock import FileLock

from cade.agent.types import LocationRenderIntent, ToolInput, ToolOutput, ToolSpec
from cade.harness.session import SessionHistory

_MAX_FILE_BYTES = 1_000_000
_SOURCES_START = "<!-- cade:memory:sources -->"
_SOURCES_END = "<!-- /cade:memory:sources -->"


def build_save_memory_tool(
    project_root: Path,
    history: SessionHistory,
    cancel_event: threading.Event | None = None,
) -> ToolSpec:
    """只在显式工具调用中检查来源并保存文件。"""
    root = project_root.resolve()

    def save(
        data: ToolInput,
        _on_update: Callable[[str], None] | None = None,
    ) -> ToolOutput:
        if cancel_event is not None and cancel_event.is_set():
            raise ValueError("Tool cancelled")
        path = _memory_path(root, data.get("path"))
        markdown = data.get("markdown")
        if not isinstance(markdown, str) or not markdown.strip():
            raise ValueError("markdown must be nonempty text")
        sources = _normalize_sources(history, data.get("sources"))
        content = _with_sources_footer(markdown, sources)
        if len(content.encode("utf-8")) > _MAX_FILE_BYTES:
            raise ValueError("Memory exceeds the ordinary text-file size limit")
        expected = data.get("expected_content")
        if expected is not None and not isinstance(expected, str):
            raise ValueError("expected_content must be the exact previous file text")
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = path.parent / ".save.lock"
        if lock_path.is_symlink():
            raise ValueError("Memory lock cannot use a symlink")
        with FileLock(str(lock_path), timeout=10):
            _memory_path(root, data.get("path"))
            if path.exists() and path.stat().st_size > _MAX_FILE_BYTES:
                raise ValueError("Existing Memory exceeds the text-file size limit")
            current = path.read_text(encoding="utf-8") if path.exists() else None
            if current != expected:
                raise ValueError(
                    "Memory changed or already exists; read it before replacing"
                )
            if cancel_event is not None and cancel_event.is_set():
                raise ValueError("Tool cancelled")
            _atomic_write(path, content, create=current is None)
        return ToolOutput(
            f"Saved Memory: {path.relative_to(root).as_posix()}\n"
            "Sources exist; their interpretation and current applicability were not verified.",
            render_intent=LocationRenderIntent(path=str(path)),
        )

    return ToolSpec(
        name="save_memory",
        description=(
            "Save a workspace Memory Markdown file with explicit history sources. "
            "This validates references, not your conclusions. No model calls are made "
            "inside this tool. To replace a file, read it and supply exact expected_content."
            " Full markdown may include the reserved source footer; it will be replaced."
        ),
        input_hint=(
            'JSON: {"path":".cade/memory/timeout.md","markdown":"# ...",'
            '"sources":[{"session_id":"...","entry_id":"..."}]}'
        ),
        handler=save,
        action_profile=("write", "path"),
        schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "markdown": {"type": "string"},
                "sources": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "session_id": {"type": "string"},
                            "entry_id": {"type": "string"},
                        },
                        "required": ["entry_id"],
                        "additionalProperties": False,
                    },
                },
                "expected_content": {"type": "string"},
            },
            "required": ["path", "markdown", "sources"],
            "additionalProperties": False,
        },
        prompt_guidelines=(
            (
                "Memory lives in .cade/memory/. Do not scan or read it by default. "
                "For explicit history needs or expensive repeated investigation, use ordinary "
                "bash with rg on that exact directory, then read files and dereference "
                "history sources "
                "as needed and check current code/configuration/tests before applying a conclusion."
            ),
            (
                "Save only costly, reusable, evidenced knowledge. Explain conditions, cause, "
                "observed results and how to check applicability in Markdown. NOTE.md owns "
                "task progress; Skills own methods; repository instructions own rules. "
                "Supply source entry IDs explicitly; omitted session_id means the current session. "
                "Git HEAD does not describe uncommitted code or prove applicability."
            ),
        ),
    )


def _with_sources_footer(markdown: str, sources: list[str]) -> str:
    """只替换末尾保留标记内的来源，不解析 Agent 正文。"""
    body, marker, footer = markdown.partition(_SOURCES_START + "\n")
    if marker:
        if (
            not body.endswith("\n")
            or _SOURCES_END in body
            or _SOURCES_START in body + footer
            or footer.count(_SOURCES_END) != 1
            or not footer.rstrip().endswith("\n" + _SOURCES_END)
        ):
            raise ValueError("Invalid reserved source footer; keep it at the end")
    elif _SOURCES_START in body or _SOURCES_END in body:
        raise ValueError("Invalid reserved source footer markers")
    return (
        body.rstrip()
        + "\n\n"
        + _SOURCES_START
        + "\n## Sources\n"
        + "\n".join(sources)
        + "\n"
        + _SOURCES_END
        + "\n"
    )


def _memory_path(root: Path, value: object) -> Path:
    """目录例外只允许直接 Markdown 文件，并拒绝符号链接。"""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("path is required")
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    directory = root / ".cade" / "memory"
    if path.parent != directory or path.suffix != ".md" or path.name.startswith("."):
        raise ValueError("path must be a Markdown file directly in .cade/memory/")
    if any(part.is_symlink() for part in (root / ".cade", directory, path)):
        raise ValueError("Memory paths cannot use symlinks")
    return path


def _normalize_sources(history: SessionHistory, value: object) -> list[str]:
    """仅规范化显式引用，省略的 session 机械绑定当前调用。"""
    if not isinstance(value, list) or not value:
        raise ValueError("sources must contain explicit history references")
    lines: list[str] = []
    for source in value:
        if not isinstance(source, dict) or set(source) - {"session_id", "entry_id"}:
            raise ValueError("Each source only accepts session_id and entry_id")
        session_id = source.get("session_id", history.session_id)
        entry_id = source.get("entry_id")
        if (
            not isinstance(session_id, str)
            or not isinstance(entry_id, str)
            or not entry_id
        ):
            raise ValueError("Each source needs a session_id and entry_id")
        result = history.read(entry_id, session_id=session_id, max_chars=1)
        if result is None:
            raise ValueError(f"History source not found: {session_id}/{entry_id}")
        line = f"- session_id={session_id} entry_id={result.entry.id}"
        if line not in lines:
            lines.append(line)
    return lines


def _atomic_write(path: Path, content: str, *, create: bool) -> None:
    """同目录临时文件提交；新增文件不覆盖并发创建。"""
    descriptor, temporary = tempfile.mkstemp(prefix=".memory-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if create:
            os.link(temporary, path)
        else:
            os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
