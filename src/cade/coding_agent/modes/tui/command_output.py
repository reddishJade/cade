"""命令输出的终端适配。"""

from __future__ import annotations

from .markdown import TerminalMarkdownRenderer


class TerminalCommandOutput:
    def write(self, text: str = "", *, end: str = "\n") -> None:
        print(text, end=end, flush=True)

    def render(self, text: str) -> None:
        TerminalMarkdownRenderer().render(text)
