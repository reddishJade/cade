"""主屏幕文档渲染：终端拥有历史，prompt-toolkit 绘制可见尾部。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from prompt_toolkit.application import Application
from prompt_toolkit.data_structures import Size
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.layout import Layout
from prompt_toolkit.renderer import Renderer, print_formatted_text

from .rendering import render_line_fragments


def _line_fragments(lines: list[str]) -> StyleAndTextTuples:
    """转换物理行，保留每行的样式和边界。"""
    fragments: StyleAndTextTuples = []
    for line in lines:
        fragments.extend(render_line_fragments(line))
        fragments.append(("", "\n"))
    return fragments


class InlineRenderer(Renderer):
    """保留完整文档；可见尾部增量绘制，历史变化时清屏重放。"""

    def __init__(self, renderer: Renderer) -> None:
        super().__init__(
            style=renderer.style,
            output=renderer.output,
            full_screen=False,
            mouse_support=False,
            cpr_not_supported_callback=renderer.cpr_not_supported_callback,
        )
        self._document: list[str] = []
        self._visible_height = 0
        self._printed_lines: list[str] = []
        self._transcript_size: Size | None = None

    def prepare(self, lines: list[str], visible_height: int) -> StyleAndTextTuples:
        """记录当前完整文档，并返回交给布局绘制的尾部。"""
        self._document = lines
        self._visible_height = visible_height
        start = max(0, len(lines) - visible_height)
        fragments = _line_fragments(lines[start:])
        if fragments:
            fragments.pop()
        return fragments

    def render(
        self, app: Application[Any], layout: Layout, is_done: bool = False
    ) -> None:
        with self._synchronized_output():
            self._sync_transcript()
            super().render(app, layout, is_done=is_done)

    def finish(self) -> None:
        """退出前把可见文档尾部写入终端，随后只清理输入和状态区。"""
        self._visible_height = 0
        with self._synchronized_output():
            self._sync_transcript()

    def _sync_transcript(self) -> None:
        size = self.output.get_size()
        end = max(0, len(self._document) - self._visible_height)
        prefix = self._document[:end]
        printed_count = len(self._printed_lines)
        rebuild = (
            prefix[:printed_count] != self._printed_lines
            or bool(self._printed_lines)
            and size != self._transcript_size
        )
        self._transcript_size = size
        if not rebuild and len(prefix) == printed_count:
            return

        # 先清理旧布局，让新增历史从布局原点连续写入。
        super().erase()
        if rebuild:
            # 和 Pi 主屏幕渲染器一样，重建历史而非尝试定位屏幕外的行。
            self.output.erase_screen()
            self.output.cursor_goto(0, 0)
            self.output.write_raw("\x1b[3J")
            printed_count = 0
        print_formatted_text(
            self.output,
            _line_fragments(prefix[printed_count:]),
            self.style,
        )
        self._printed_lines = prefix
        self.request_absolute_cursor_position()

    @contextmanager
    def _synchronized_output(self) -> Iterator[None]:
        """将历史写入和布局更新放在同一个终端同步帧内。"""
        self.output.write_raw("\x1b[?2026h")
        try:
            yield
        finally:
            self.output.write_raw("\x1b[?2026l")
            self.output.flush()
