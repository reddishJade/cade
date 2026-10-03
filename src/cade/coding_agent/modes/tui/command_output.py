"""命令输出与选择的终端适配。"""

from __future__ import annotations

from collections.abc import Sequence

import questionary

from cade.coding_agent.interaction.commands import Choice

from .markdown import TerminalMarkdownRenderer
from .ptk_patch import safe_select


class TerminalCommandOutput:
    def write(self, text: str = "", *, end: str = "\n") -> None:
        print(text, end=end, flush=True)

    def render(self, text: str) -> None:
        TerminalMarkdownRenderer().render(text)

    def select[T](
        self, title: str, choices: Sequence[Choice[T]], default: T | None = None
    ) -> T | None:
        return safe_select(
            title,
            choices=[
                questionary.Choice(title=item.title, value=item.value)
                for item in choices
            ],
            default=default,
        )
