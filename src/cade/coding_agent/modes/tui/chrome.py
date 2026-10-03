"""终端工作台的配色、欢迎信息和自适应状态行。"""

from __future__ import annotations

from pathlib import Path

from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.formatted_text.utils import fragment_list_width
from prompt_toolkit.utils import get_cwidth

TUI_STYLES: dict[str, str] = {
    "": "#c9d1d9",
    "welcome": "#58a6ff bold",
    "welcome-hint": "#8b949e",
    "resource-heading": "#d29922",
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
    "input": "bg:#21262d #c9d1d9",
    "prompt-marker": "#58a6ff bold",
    "scrollbar.background": "",
    "scrollbar.button": "",
    "auto-suggestion": "#6e7681",
}


CADE_LOGO = (
    "█▀▀▀ █▀▀█ █▀▀▄ █▀▀▀",
    "█    █▄▄█ █  █ █▀▀ ",
    "█▄▄▄ █  █ █▄▄▀ █▄▄▄",
)


def welcome_text(
    *,
    version: str,
    tools: tuple[str, ...],
    instructions: tuple[Path, ...],
    skills: tuple[str, ...],
    width: int,
    compact: bool = False,
) -> str:
    """欢迎区展示当前运行时资源；短终端减少装饰和快捷键行数。"""
    if compact:
        lines = [f"cade v{version}", "/ commands · @ files · Ctrl+J newline"]
    else:
        lines = [*CADE_LOGO]
        lines[-1] += f"  v{version}"
        lines.extend(
            [
                "",
                "Describe a task to get started.",
                "/ commands · @ files · ! shell · $ skills · ↑/↓ history",
                "Enter send / queue · Alt+Enter steer · Ctrl+J newline",
                "Ctrl+T thinking · Ctrl+O tool details · ? help",
                "cade -c continue · /resume choose history",
            ]
        )
    resources = [
        ("Tools", ", ".join(tools)),
        ("Context", ", ".join(compact_path(path) for path in instructions)),
        ("Skills", ", ".join(skills)),
    ]
    for name, value in resources:
        if not value:
            continue
        if compact:
            lines.append(f"[{name}] {value}")
        else:
            detail = fit_text(value, width - 2, keep_end=name == "Context")
            lines.extend(["", f"[{name}]", f"  {detail}"])
    return "\n".join(fit_text(line, width) for line in lines)


def welcome_ansi_lines(text: str) -> list[str]:
    """欢迎区按字标、资源标题和辅助说明分别着色。"""
    result: list[str] = []
    for line in text.splitlines():
        if line.startswith(("█", "cade v")):
            logo, separator, version = line.partition("  v")
            rendered = f"\x1b[38;2;88;166;255;1m{logo}"
            if separator:
                rendered += f"\x1b[0;38;2;139;148;158m  v{version}"
        elif line.startswith("["):
            heading, separator, detail = line.partition("]")
            rendered = f"\x1b[38;2;210;153;34m{heading}{separator}"
            if detail:
                rendered += f"\x1b[38;2;139;148;158m{detail}"
        else:
            rendered = f"\x1b[38;2;139;148;158m{line}"
        result.append(f"{rendered}\x1b[0m")
    return result


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
