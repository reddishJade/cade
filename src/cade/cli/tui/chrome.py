"""终端工作台的配色、欢迎信息和自适应状态行。"""

from __future__ import annotations

from pathlib import Path

from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.formatted_text.utils import fragment_list_width
from prompt_toolkit.utils import get_cwidth

TUI_STYLES: dict[str, str] = {
    "": "#c9d1d9",
    "welcome": "#58a6ff bold",
    "user": "bg:#21262d #c9d1d9",
    "command": "#58a6ff",
    "thinking": "#8b949e italic",
    "tool": "#8b949e",
    "tool-title": "#8b949e",
    "error": "#ff7b72",
    "border": "#484f58",
    "completion-menu": "bg:#161b22 #8b949e",
    "completion-menu.completion": "bg:#161b22 #c9d1d9",
    "completion-menu.completion.current": "bg:#21262d #58a6ff bold",
    "completion-menu.meta.completion": "bg:#161b22 #8b949e",
    "completion-menu.meta.completion.current": "bg:#21262d #c9d1d9",
    "radio-selected": "#58a6ff bold",
    "model-current": "#58a6ff bold",
    "choice-desc": "#8b949e",
    "status": "#8b949e",
    "status-accent": "#58a6ff",
    "status-mode": "#bc8cff",
    "input-border": "#484f58",
    "prompt-marker": "#58a6ff bold",
    "scrollbar.background": "",
    "scrollbar.button": "",
    "auto-suggestion": "#6e7681",
}


def welcome_text() -> str:
    return (
        "✦ cade\n\n"
        "Describe a task to get started.\n"
        "/ commands   @ files   ! shell   $ skills\n"
        "Enter send / queue   Alt+Enter steer   Ctrl+J newline\n"
        "Ctrl+T thinking   Ctrl+O tool details   ? help\n"
        "Continue later with cade -c; choose history with /resume."
    )


def compact_path(path: Path) -> str:
    """项目路径使用波浪号缩短用户主目录。"""
    try:
        return "~/" + path.relative_to(Path.home()).as_posix()
    except ValueError:
        return str(path)


def fit_text(text: str, width: int, *, keep_end: bool = False) -> str:
    """按终端显示宽度裁剪，保留中文与组合字符。"""
    if width <= 0:
        return ""
    if get_cwidth(text) <= width:
        return text
    characters = reversed(text) if keep_end else iter(text)
    result: list[str] = []
    used = 1
    for character in characters:
        size = get_cwidth(character)
        if used + size > width:
            break
        result.append(character)
        used += size
    if keep_end:
        return "…" + "".join(reversed(result))
    return "".join(result) + "…"


def status_line(
    left: str,
    right: str,
    width: int,
    *,
    left_style: str = "class:status",
    right_style: str = "class:status",
    keep_left_end: bool = False,
) -> StyleAndTextTuples:
    """为左右状态信息保留独立空间，窄屏时优先保留右侧。"""
    width = max(1, width)
    right = fit_text(right, max(0, width - min(12, width // 3) - 2))
    left_width = width - get_cwidth(right) - (2 if right else 0)
    left = fit_text(left, left_width, keep_end=keep_left_end)
    fragments: StyleAndTextTuples = [(left_style, left)]
    if right:
        padding = width - fragment_list_width(fragments) - get_cwidth(right)
        fragments.extend([("", " " * max(0, padding)), (right_style, right)])
    return fragments
