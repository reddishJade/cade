"""CLI / TUI 的 Ctrl+C / Ctrl+D 双击退出判定测试。"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

from cade.cli.commands import ReplState
from cade.cli.exit_keys import (
    EXIT_CONFIRM_WINDOW_SECONDS,
    ExitKeyAction,
    ExitKeyKind,
    exit_confirm_hint,
    exit_confirmed,
    resolve_exit_key_action,
)
from cade.cli.repl import _read_repl_text


def _state(*, pending: float = 0.0, key: str = "") -> ReplState:
    state = ReplState()
    state.exit_pending = pending
    state.exit_pending_key = key
    return state


def test_first_ctrl_c_on_empty_buffer_defers_to_reader() -> None:
    """空输入首次 Ctrl+C 只抛中断，由读取端记录待确认状态，避免一击即退。"""
    action = resolve_exit_key_action(
        0.0, "", 100.0, buffer_text="", key=ExitKeyKind.INTERRUPT
    )

    assert action is ExitKeyAction.ARM


def test_ctrl_c_with_text_clears_and_arms() -> None:
    action = resolve_exit_key_action(
        0.0, "", 100.0, buffer_text="hello", key=ExitKeyKind.INTERRUPT
    )

    assert action is ExitKeyAction.CLEAR_AND_ARM


def test_second_ctrl_c_inside_window_confirms() -> None:
    action = resolve_exit_key_action(
        100.0,
        ExitKeyKind.INTERRUPT.value,
        101.0,
        buffer_text="",
        key=ExitKeyKind.INTERRUPT,
    )

    assert action is ExitKeyAction.INTERRUPT


def test_ctrl_d_after_ctrl_c_does_not_confirm() -> None:
    """混用按键不算确认：换键只重新进入该键的确认窗口。"""
    action = resolve_exit_key_action(
        100.0,
        ExitKeyKind.INTERRUPT.value,
        101.0,
        buffer_text="",
        key=ExitKeyKind.EOF,
    )

    assert action is ExitKeyAction.ARM


def test_ctrl_c_after_ctrl_d_does_not_confirm() -> None:
    action = resolve_exit_key_action(
        100.0,
        ExitKeyKind.EOF.value,
        101.0,
        buffer_text="",
        key=ExitKeyKind.INTERRUPT,
    )

    assert action is ExitKeyAction.ARM


def test_second_ctrl_d_inside_window_confirms() -> None:
    action = resolve_exit_key_action(
        100.0,
        ExitKeyKind.EOF.value,
        101.0,
        buffer_text="",
        key=ExitKeyKind.EOF,
    )

    assert action is ExitKeyAction.EOF


def test_ctrl_d_with_text_is_ignored_until_confirmation() -> None:
    ignored = resolve_exit_key_action(
        0.0, "", 100.0, buffer_text="hello", key=ExitKeyKind.EOF
    )
    confirmed = resolve_exit_key_action(
        100.0,
        ExitKeyKind.EOF.value,
        101.0,
        buffer_text="hello",
        key=ExitKeyKind.EOF,
    )

    assert ignored is ExitKeyAction.IGNORE
    assert confirmed is ExitKeyAction.EOF


def test_exit_confirmed_requires_same_key_and_live_window() -> None:
    assert exit_confirmed(
        100.0, ExitKeyKind.INTERRUPT.value, 101.0, ExitKeyKind.INTERRUPT
    )
    assert not exit_confirmed(
        100.0, ExitKeyKind.INTERRUPT.value, 101.0, ExitKeyKind.EOF
    )
    assert not exit_confirmed(
        100.0,
        ExitKeyKind.INTERRUPT.value,
        100.0 + EXIT_CONFIRM_WINDOW_SECONDS,
        ExitKeyKind.INTERRUPT,
    )
    assert not exit_confirmed(0.0, "", 100.0, ExitKeyKind.INTERRUPT)


def test_exit_confirm_hint_names_the_key() -> None:
    assert "Ctrl+C" in exit_confirm_hint(ExitKeyKind.INTERRUPT)
    assert "Ctrl+D" in exit_confirm_hint(ExitKeyKind.EOF)


def test_read_repl_text_requires_same_key_twice() -> None:
    """Ctrl+C 之后换成 Ctrl+D 不算确认，连续两次 Ctrl+D 才退出。"""
    state = ReplState()
    session = MagicMock()
    session.prompt.side_effect = [KeyboardInterrupt(), EOFError(), EOFError()]
    store = MagicMock()

    first = _read_repl_text(state, session, store)
    second = _read_repl_text(state, session, store)
    third = _read_repl_text(state, session, store)

    assert first == (None, False)
    assert second == (None, False)
    assert third == (None, True)


def test_read_repl_text_rearms_after_confirm_window() -> None:
    state = _state(
        pending=time.time() - EXIT_CONFIRM_WINDOW_SECONDS - 1.0,
        key=ExitKeyKind.INTERRUPT.value,
    )
    session = MagicMock()
    session.prompt.side_effect = KeyboardInterrupt()

    text, should_exit = _read_repl_text(state, session, MagicMock())

    assert text is None
    assert should_exit is False
    assert state.exit_pending > time.time() - EXIT_CONFIRM_WINDOW_SECONDS
