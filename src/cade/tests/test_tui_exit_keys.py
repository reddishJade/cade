"""TUI Ctrl+C / Ctrl+D 双击退出的行为测试。"""

from __future__ import annotations

from pathlib import Path
from time import perf_counter
from unittest.mock import patch

from prompt_toolkit.input.base import DummyInput
from prompt_toolkit.output import DummyOutput

from cade.cli.exit_keys import EXIT_CONFIRM_WINDOW_SECONDS, ExitKeyKind
from cade.cli.tui.app import _CadeTui
from cade.harness.session import SessionStore


class _FakeAgent:
    current_mode = "act"
    approval_policy = "never"
    session_id = ""


class _FakeApp:
    def __init__(self, store: SessionStore) -> None:
        self.session_store = store
        self.agent = _FakeAgent()
        self.registry: tuple[object, ...] = ()


def _build_tui(tmp_path: Path) -> _CadeTui:
    root = tmp_path.resolve()
    store = SessionStore(root / ".cade" / "sessions")
    return _CadeTui(
        _FakeApp(store),
        root,
        input=DummyInput(),
        output=DummyOutput(),
    )


def _exit_patches(tui: _CadeTui):
    return (
        patch("cade.cli.tui.app.print_saved_conversation"),
        patch.object(type(tui._application), "exit"),
    )


def test_tui_ctrl_c_press_twice_exits(tmp_path: Path) -> None:
    tui = _build_tui(tmp_path)
    print_patch, exit_patch = _exit_patches(tui)
    with print_patch, exit_patch as exit_mock:
        tui._cancel_key(None)

        assert tui._exit_pending > 0
        assert any("press Ctrl+C again" in entry.text for entry in tui._state.log)
        exit_mock.assert_not_called()

        tui._cancel_key(None)

    exit_mock.assert_called_once()


def test_tui_ctrl_d_press_twice_exits(tmp_path: Path) -> None:
    tui = _build_tui(tmp_path)
    print_patch, exit_patch = _exit_patches(tui)
    with print_patch, exit_patch as exit_mock:
        tui._eof_key(None)

        assert tui._exit_pending > 0
        assert any("press Ctrl+D again" in entry.text for entry in tui._state.log)
        exit_mock.assert_not_called()

        tui._eof_key(None)

    exit_mock.assert_called_once()


def test_tui_ctrl_c_then_ctrl_d_does_not_exit(tmp_path: Path) -> None:
    tui = _build_tui(tmp_path)
    print_patch, exit_patch = _exit_patches(tui)
    with print_patch, exit_patch as exit_mock:
        tui._cancel_key(None)
        tui._eof_key(None)

        exit_mock.assert_not_called()
        assert tui._exit_pending_key == ExitKeyKind.EOF.value

        tui._eof_key(None)

    exit_mock.assert_called_once()


def test_tui_ctrl_c_clears_input_and_arms(tmp_path: Path) -> None:
    tui = _build_tui(tmp_path)
    tui._input.text = "hello"
    print_patch, exit_patch = _exit_patches(tui)
    with print_patch, exit_patch as exit_mock:
        tui._cancel_key(None)

    assert tui._input.text == ""
    assert tui._exit_pending > 0
    exit_mock.assert_not_called()


def test_tui_ctrl_d_ignores_non_empty_input(tmp_path: Path) -> None:
    tui = _build_tui(tmp_path)
    tui._input.text = "hello"
    print_patch, exit_patch = _exit_patches(tui)
    with print_patch, exit_patch as exit_mock:
        tui._eof_key(None)

    assert tui._exit_pending == 0.0
    exit_mock.assert_not_called()


def test_tui_exit_confirmation_expires(tmp_path: Path) -> None:
    tui = _build_tui(tmp_path)
    tui._exit_pending = perf_counter() - EXIT_CONFIRM_WINDOW_SECONDS - 1.0
    print_patch, exit_patch = _exit_patches(tui)
    with print_patch, exit_patch as exit_mock:
        tui._cancel_key(None)

    exit_mock.assert_not_called()
    assert tui._exit_pending > perf_counter() - 1.0


def test_tui_typing_cancels_pending_exit(tmp_path: Path) -> None:
    tui = _build_tui(tmp_path)
    print_patch, exit_patch = _exit_patches(tui)
    with print_patch, exit_patch:
        tui._cancel_key(None)
        tui._on_input_text_inserted(None)

    assert tui._exit_pending == 0.0
    assert tui._exit_pending_key == ""
