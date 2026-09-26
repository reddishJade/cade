"""TUI 自定义 prompt_toolkit 组件：输入栏高亮器、伪 PromptSession。"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import cast

from prompt_toolkit.application import get_app
from prompt_toolkit.data_structures import Point
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.layout.controls import FormattedTextControl, UIContent
from prompt_toolkit.layout.menus import CompletionsMenuControl
from prompt_toolkit.lexers import Lexer
from prompt_toolkit.mouse_events import MouseEvent, MouseEventType
from prompt_toolkit.utils import get_cwidth

from ..commands import PromptText
from .chrome import fit_text

_file_ref_pattern = re.compile(r"(?<!\S)@([^\s]+)")


class TuiOutputControl(FormattedTextControl):
    """处理 inline TUI 输出区域的鼠标滚轮。"""

    def __init__(self, on_scroll: Callable[[int], None]) -> None:
        super().__init__(text="", focusable=False)
        self._on_scroll = on_scroll

    def mouse_handler(self, mouse_event: MouseEvent) -> object:
        if mouse_event.event_type == MouseEventType.SCROLL_UP:
            self._on_scroll(3)
            return None
        if mouse_event.event_type == MouseEventType.SCROLL_DOWN:
            self._on_scroll(-3)
            return None
        return super().mouse_handler(mouse_event)


class TuiInputLexer(Lexer):
    """高亮 TUI 输入栏中的 @file 引用。"""

    def lex_document(self, document: object) -> Callable[[int], StyleAndTextTuples]:
        lines: list[str] = (
            getattr(document, "lines", None)
            or str(getattr(document, "text", "")).splitlines()
        )
        if not lines:
            lines = [""]

        def get_line(line_number: int) -> StyleAndTextTuples:
            if line_number < 0 or line_number >= len(lines):
                return []
            return self._highlight(lines[line_number])

        return get_line

    @staticmethod
    def _highlight(line: str) -> StyleAndTextTuples:
        frags: list[tuple[str, str]] = []
        cursor = 0
        for m in _file_ref_pattern.finditer(line, cursor):
            if m.start() > cursor:
                frags.append(("", line[cursor : m.start()]))
            frags.append(("fg:ansicyan bold", m.group(0)))
            cursor = m.end()
        if cursor < len(line):
            frags.append(("", line[cursor:]))
        if not frags:
            frags.append(("", line))
        return cast(StyleAndTextTuples, frags)


class TuiCompletionControl(CompletionsMenuControl):
    """将补全名称与说明限制在窗口宽度内，并突出当前选项。"""

    def create_content(self, width: int, height: int) -> UIContent:
        state = get_app().current_buffer.complete_state
        if state is None or not state.completions:
            return UIContent()
        index = state.complete_index or 0
        name_width = min(
            max(get_cwidth(item.display_text) for item in state.completions),
            max(1, width // 2 - 2),
        )

        def get_line(line: int) -> StyleAndTextTuples:
            item = state.completions[line]
            suffix = ".current" if line == index else ""
            name = fit_text(item.display_text, name_width)
            padding = " " * max(0, name_width - get_cwidth(name) + 2)
            description = fit_text(item.display_meta_text, width - name_width - 4)
            return [
                (
                    f"class:completion-menu.completion{suffix}",
                    f"{'›' if line == index else ' '} {name}{padding}",
                ),
                (f"class:completion-menu.meta.completion{suffix}", description),
            ]

        return UIContent(
            get_line=get_line,
            cursor_position=Point(x=0, y=index),
            line_count=len(state.completions),
        )


def tui_input_prompt(
    awaiting_denial_suggestion: bool, is_shell_command: bool
) -> StyleAndTextTuples:
    """返回输入提示符；shell 命令模式时强调输入标记。"""
    marker_style = "class:prompt-marker" if is_shell_command else ""
    if awaiting_denial_suggestion:
        return [("", "Tell model what to do "), (marker_style, "> ")]
    return [(marker_style, "> ")]


class TuiPromptSession:
    """TUI 占位 PromptSession——CLI 的 PromptSession 适配接口。

    实际 TUI 使用 TextArea 输入，不需要 CLI 的 PromptSession。
    """

    def prompt(self, prompt_text: PromptText) -> str:
        _ = prompt_text
        return ""
