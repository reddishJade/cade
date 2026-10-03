"""将宿主无关的补全候选转换为终端组件。"""

from __future__ import annotations

from collections.abc import Iterable

from prompt_toolkit.auto_suggest import AutoSuggest, Suggestion
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import FormattedText

from cade.coding_agent.interaction.completion import CompletionEngine


class TuiCompleter(Completer):
    def __init__(self, engine: CompletionEngine) -> None:
        self.engine = engine

    @property
    def command_args(self) -> dict[str, str]:
        return self.engine.command_args

    def get_completions(
        self, document: Document, complete_event: CompleteEvent
    ) -> Iterable[Completion]:
        for item in self.engine.complete(document.text_before_cursor):
            yield Completion(
                item.text,
                start_position=item.start_position,
                display_meta=FormattedText([("fg:ansibrightblack", item.display_meta)]),
            )


class CommandArgsSuggester(AutoSuggest):
    def __init__(self, command_args: dict[str, str]) -> None:
        self._command_args = command_args

    def get_suggestion(self, buffer: Buffer, document: Document) -> Suggestion | None:
        text = document.text.strip()
        if text in self._command_args:
            return Suggestion(f" {self._command_args[text]}")
        return None
