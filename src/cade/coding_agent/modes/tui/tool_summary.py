from __future__ import annotations

import json
from typing import Any

from rich.text import Text

from cade.agent.types import ToolInput

from .thinking import single_line_preview

CLI_COLOR_DIM = "grey50"
CLI_COLOR_TOOL = "yellow"


def tool_call_text(name: str, label: str, raw_input: ToolInput | str) -> Text:
    """渲染工具调用摘要。"""
    if isinstance(raw_input, dict) and name == "delegate":
        rendered = _subagent_list_text(raw_input)
        if rendered is not None:
            return rendered
    return Text(f"  → {label}", style=CLI_COLOR_TOOL)


def _subagent_list_text(raw_input: ToolInput) -> Text | None:
    tasks = _subagent_tasks(raw_input)
    if not tasks:
        return None
    text = Text(f"  → Subagent tasks ({len(tasks)})", style=CLI_COLOR_TOOL)
    for index, task in enumerate(tasks, start=1):
        label = str(task.get("description", "")).strip() or f"task-{index}"
        agent_type = str(task.get("subagent_type", "coding")).strip() or "coding"
        text.append("\n")
        text.append(f"    [{index}] ", style=CLI_COLOR_DIM)
        text.append(label, style=CLI_COLOR_TOOL)
        text.append(f" [{agent_type}]", style=CLI_COLOR_DIM)
    return text


def _shorten_path(p: str) -> str:
    """缩短路径，保留尾部和关键部分。"""
    if not p or p == ".":
        return "."
    p = p.replace("\\", "/")
    parts = p.split("/")
    if len(parts) <= 3:
        return p
    # src/cade/coding_agent/app.py → src/cade/.../app.py
    return f"{parts[0]}/{parts[1]}/.../{parts[-1]}"


def brief_input(name: str, raw_input: ToolInput | str) -> str:
    """从工具输入中提取简短的人类可读摘要。"""
    if not isinstance(raw_input, dict):
        return single_line_preview(f"{name}: {raw_input}") if raw_input else name

    # ── 各工具类型特化格式化 ──
    if name == "bash":
        cmd = raw_input.get("command") or raw_input.get("input") or ""
        return single_line_preview(f"$ {cmd}") if cmd else name

    if name == "read":
        path = _shorten_path(
            str(
                raw_input.get(
                    "file_path", raw_input.get("path", raw_input.get("input", ""))
                )
            )
        )
        off = raw_input.get("offset")
        lim = raw_input.get("limit")
        suffix = f":{off}" if off else ""
        if lim:
            suffix += f"-{off + lim - 1}" if off else f" ({lim} lines)"
        return single_line_preview(f"read {path}{suffix}")

    if name == "write":
        path = _shorten_path(str(raw_input.get("file_path", raw_input.get("path", ""))))
        content = str(raw_input.get("content", ""))
        lines = content.count("\n") + 1 if content else 0
        return single_line_preview(
            f"write {path}" + (f" ({lines} lines)" if lines else "")
        )

    if name == "edit":
        path = _shorten_path(str(raw_input.get("file_path", raw_input.get("path", ""))))
        return single_line_preview(f"edit {path}")

    if name == "ls":
        path = _shorten_path(str(raw_input.get("path", ".")))
        return single_line_preview(f"ls {path}")

    if name in ("glob", "find"):
        pattern = str(raw_input.get("pattern", raw_input.get("path", "*")))
        path = _shorten_path(str(raw_input.get("path", ".")))
        return single_line_preview(
            f"glob {pattern}" + (f" in {path}" if path != "." else "")
        )

    if name == "grep":
        pattern = str(raw_input.get("pattern", ""))
        path = _shorten_path(str(raw_input.get("path", raw_input.get("include", "."))))
        return single_line_preview(
            f"grep /{pattern}/" + (f" in {path}" if path != "." else "")
        )

    if name == "delegate":
        tasks = _subagent_tasks(raw_input)
        if tasks:
            return f"delegate tasks ({len(tasks)})"
        desc = raw_input.get("description", "")
        return single_line_preview(f"delegate: {desc}") if desc else name

    if name == "websearch":
        query = raw_input.get("query", raw_input.get("input", ""))
        return single_line_preview(f"search: {query}") if query else name

    if name == "webfetch":
        url = raw_input.get("url", raw_input.get("input", ""))
        return single_line_preview(f"fetch: {url}") if url else name

    # ── 兜底：key=value 列表 ──
    parts = [
        f"{k}={json.dumps(v, ensure_ascii=False)}"
        for k, v in raw_input.items()
        if v not in (None, "", [], {})
    ]
    if parts:
        return single_line_preview(f"{name}: {', '.join(parts)}")
    if raw_input:
        k, v = next(iter(raw_input.items()))
        return single_line_preview(f"{name}: {k}={v}")
    return name


def _subagent_tasks(raw_input: ToolInput) -> list[dict[str, Any]]:
    tasks = raw_input.get("tasks", [])
    if not isinstance(tasks, list):
        return []
    return [item for item in tasks if isinstance(item, dict)]
