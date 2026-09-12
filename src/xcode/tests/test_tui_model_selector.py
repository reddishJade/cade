"""TUI 原生模型选择器测试。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from prompt_toolkit.input.base import DummyInput
from prompt_toolkit.formatted_text import fragment_list_to_text, to_formatted_text
from prompt_toolkit.output import DummyOutput

from xcode.cli.repl_settings import AvailableModelEntry
from xcode.cli.tui.app import _XcodeTui
from xcode.harness.session import SessionStore


class _FakeAgent:
    current_mode = "act"
    approval_policy = "never"
    session_id = ""


class _ModelApp:
    def __init__(self, store: SessionStore) -> None:
        self.session_store = store
        self.agent = _FakeAgent()
        self.registry: tuple[object, ...] = ()
        self.model = "old-model"
        self.transport = "old_chat"
        self.model_calls: list[tuple[str, str | None]] = []

    def get_model_info(self) -> dict[str, str]:
        return {"model": self.model, "transport": self.transport}

    def set_model(
        self,
        *,
        model: str,
        transport: str | None = None,
        profile: str = "main",
        **_kwargs: object,
    ) -> str:
        assert profile == "main"
        self.model_calls.append((model, transport))
        self.model = model
        if transport is not None:
            self.transport = transport
        return model


def _build_tui(tmp_path: Path) -> tuple[_XcodeTui, _ModelApp]:
    root = tmp_path.resolve()
    app = _ModelApp(SessionStore(root / ".xcode" / "sessions"))
    tui = _XcodeTui(app, root, input=DummyInput(), output=DummyOutput())
    return tui, app


def _model_entry() -> AvailableModelEntry:
    return AvailableModelEntry("new-model", "new_chat", "new", "[new]")


def test_tui_model_command_uses_native_escape_menu(tmp_path: Path) -> None:
    tui, app = _build_tui(tmp_path)

    with patch(
        "xcode.cli.repl_settings.get_available_model_entries",
        return_value=[_model_entry()],
    ):
        assert tui._show_native_command_choice("/model") is True

    request = tui._state.pending_command_choice
    assert request is not None
    assert [label for label, _value in request.choices] == [
        "new-model            [new]",
        "输入自定义模型名称...",
    ]

    tui._cancel_key(None)

    assert tui._state.pending_command_choice is None
    assert app.model_calls == []


def test_tui_model_command_highlights_current_model(tmp_path: Path) -> None:
    tui, app = _build_tui(tmp_path)
    entry = _model_entry()
    app.model = entry.model
    app.transport = entry.transport

    with patch(
        "xcode.cli.repl_settings.get_available_model_entries",
        return_value=[entry],
    ):
        tui._show_native_command_choice("/model")

    request = tui._state.pending_command_choice
    assert request is not None
    title = request.choices[0][0]
    fragments = to_formatted_text(title)
    assert fragment_list_to_text(fragments) == "new-model            [new]"
    assert fragments[0][0] == "class:model-current"


def test_tui_model_command_switches_selected_model(tmp_path: Path) -> None:
    tui, app = _build_tui(tmp_path)
    entry = _model_entry()

    with patch(
        "xcode.cli.repl_settings.get_available_model_entries", return_value=[entry]
    ):
        tui._show_native_command_choice("/model")
        tui._command_choices.current_value = entry
        tui._accept_command_choice()

    assert app.model_calls == [("new-model", "new_chat")]
    assert any("已成功切换至模型: new-model" in item.text for item in tui._state.log)


def test_tui_custom_model_escape_returns_to_model_menu(tmp_path: Path) -> None:
    tui, _app = _build_tui(tmp_path)

    with patch(
        "xcode.cli.repl_settings.get_available_model_entries",
        return_value=[_model_entry()],
    ):
        tui._show_native_command_choice("/model")
        tui._command_choices.current_value = "__custom__"
        tui._accept_command_choice()
        assert tui._state.pending_command_text is not None

        tui._escape_key(None)

    assert tui._state.pending_command_text is None
    assert tui._state.pending_command_choice is not None
