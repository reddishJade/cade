"""Ctrl+C / Ctrl+D 双击退出的共享判定逻辑。"""

from __future__ import annotations

from enum import Enum

# 双击 Ctrl+C / Ctrl+D 退出的确认窗口（秒）。
EXIT_CONFIRM_WINDOW_SECONDS = 3.0


class ExitKeyKind(Enum):
    """退出按键的种类；双击确认必须使用同一种按键。"""

    INTERRUPT = "c-c"
    EOF = "c-d"


class ExitKeyAction(Enum):
    """输入栏内 Ctrl+C / Ctrl+D 的处理动作。"""

    IGNORE = "ignore"
    CLEAR_AND_ARM = "clear-and-arm"
    ARM = "arm"
    INTERRUPT = "interrupt"
    EOF = "eof"


def exit_window_active(pending: float, now: float) -> bool:
    """判断双击退出的确认窗口是否仍然打开。"""
    return pending > 0 and now - pending < EXIT_CONFIRM_WINDOW_SECONDS


def exit_confirmed(
    pending: float,
    pending_key: str,
    now: float,
    key: ExitKeyKind,
) -> bool:
    """同一种按键在确认窗口内第二次触发时返回 True。"""
    return exit_window_active(pending, now) and pending_key == key.value


def resolve_exit_key_action(
    pending: float,
    pending_key: str,
    now: float,
    *,
    buffer_text: str,
    key: ExitKeyKind,
) -> ExitKeyAction:
    """决定退出按键的动作。

    只有同一种按键的第二次触发才算确认，中途换键只会重新进入确认窗口。
    中断/EOF 动作只表示按键意图；首次按键是否进入确认窗口由调用方统一
    记录，避免同一次按键既被打上标记又被当成二次确认。
    """
    if key is ExitKeyKind.EOF:
        if exit_confirmed(pending, pending_key, now, key):
            return ExitKeyAction.EOF
        if buffer_text:
            return ExitKeyAction.IGNORE
        return ExitKeyAction.ARM
    if exit_confirmed(pending, pending_key, now, key):
        return ExitKeyAction.INTERRUPT
    if buffer_text:
        return ExitKeyAction.CLEAR_AND_ARM
    return ExitKeyAction.ARM


def exit_confirm_hint(key: ExitKeyKind) -> str:
    """返回提示用户再次按同一按键退出的文案。"""
    name = "Ctrl+C" if key is ExitKeyKind.INTERRUPT else "Ctrl+D"
    return f"(press {name} again to exit)"
