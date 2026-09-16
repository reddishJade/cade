"""Windows 控制台生命周期看门狗、异常抑制与安全选择器。

职责：
1. 操作系统级 Win32 控制台控制处理器（SetConsoleCtrlHandler），确保在任何卡死/阻塞状态下均可响应 Ctrl+C 退出；
2. 抑制 prompt_toolkit 临时事件循环关闭时的句柄回调异常；
3. 终端生命周期隔离看门狗（terminal_isolated）与输入缓冲区清空；
4. 提供语义统一的可取消选择器、多选器与文本输入。
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import time
import warnings
from collections.abc import Generator, Sequence
from typing import Any, cast

from prompt_toolkit.key_binding import KeyBindings

_signal_handler_installed = False
_console_ctrl_handler_installed = False
_windows_ptk_patch_installed = False
_last_ctrl_c_time: float = 0.0
_last_win32_ctrl_c_time: float = 0.0
_global_win32_ctrl_ref: Any = None


def install_force_exit_signal_handler() -> None:
    """安装底层信号与操作系统控制台事件监听器。

    在任何情况下 3 秒内连续两次 Ctrl+C 强制退出进程，单次 Ctrl+C 打断并唤醒主线程。
    """
    global _signal_handler_installed, _console_ctrl_handler_installed
    global _global_win32_ctrl_ref
    import signal

    if not _signal_handler_installed:
        orig_handler = signal.getsignal(signal.SIGINT)

        def _sigint_handler(signum: int, frame: Any) -> None:
            global _last_ctrl_c_time
            now = time.monotonic()
            if _last_ctrl_c_time > 0 and (now - _last_ctrl_c_time) < 3.0:
                sys.stderr.write(
                    "\n\033[91m[强制退出]\033[0m 检测到连续 Ctrl+C，正在终止 Cade...\n"
                )
                sys.stderr.flush()
                os._exit(0)
            _last_ctrl_c_time = now
            if callable(orig_handler):
                orig_handler(signum, frame)
            elif orig_handler != signal.SIG_IGN:
                raise KeyboardInterrupt()

        try:
            signal.signal(signal.SIGINT, _sigint_handler)
        except (ValueError, OSError):
            pass
        else:
            _signal_handler_installed = True

    if sys.platform == "win32" and not _console_ctrl_handler_installed:
        try:
            import _thread
            import ctypes

            PHANDLER_ROUTINE = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_ulong)

            def _win32_ctrl_handler(ctrl_type: int) -> bool:
                global _last_win32_ctrl_c_time
                # CTRL_C_EVENT = 0, CTRL_BREAK_EVENT = 1
                if ctrl_type in (0, 1):
                    now = time.monotonic()
                    if (
                        _last_win32_ctrl_c_time > 0
                        and (now - _last_win32_ctrl_c_time) < 3.0
                    ):
                        sys.stderr.write(
                            "\n\033[91m[强制退出]\033[0m "
                            "检测到系统级连续 Ctrl+C，正在强制终止 Cade...\n"
                        )
                        sys.stderr.flush()
                        os._exit(0)
                    _last_win32_ctrl_c_time = now
                    try:
                        _thread.interrupt_main()
                    except (RuntimeError, OSError):
                        pass

                    return True
                elif ctrl_type == 2:  # CTRL_CLOSE_EVENT
                    os._exit(0)
                return False

            handler_ref = PHANDLER_ROUTINE(_win32_ctrl_handler)
            k32 = ctypes.windll.kernel32
            if k32.SetConsoleCtrlHandler(handler_ref, True):
                _global_win32_ctrl_ref = handler_ref
                _console_ctrl_handler_installed = True
        except (AttributeError, OSError):
            pass


def flush_console_input_buffer() -> None:
    """清空 Windows 控制台输入缓冲区中的脏按键事件。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        k32 = ctypes.windll.kernel32
        stdin_handle = k32.GetStdHandle(-10)  # STD_INPUT_HANDLE
        k32.FlushConsoleInputBuffer(stdin_handle)
    except (AttributeError, OSError):
        pass


def get_console_mode() -> int | None:
    """获取当前 Windows 控制台标准输入模式。"""
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        k32 = ctypes.windll.kernel32
        stdin_handle = k32.GetStdHandle(-10)
        mode = ctypes.c_ulong()
        if k32.GetConsoleMode(stdin_handle, ctypes.byref(mode)):
            return mode.value
    except (AttributeError, OSError):
        pass
    return None


def set_console_mode(mode: int) -> None:
    """设置 Windows 控制台标准输入模式。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        k32 = ctypes.windll.kernel32
        stdin_handle = k32.GetStdHandle(-10)
        k32.SetConsoleMode(stdin_handle, mode)
    except (AttributeError, OSError):
        pass


def restore_console_mode() -> None:
    """确保 Windows 控制台模式恢复正常行输入模式。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        k32 = ctypes.windll.kernel32
        stdin_handle = k32.GetStdHandle(-10)  # STD_INPUT_HANDLE
        mode = ctypes.c_ulong()
        if k32.GetConsoleMode(stdin_handle, ctypes.byref(mode)):
            enable_processed_input = 0x0001
            enable_line_input = 0x0002
            enable_echo_input = 0x0004
            new_mode = (
                mode.value
                | enable_processed_input
                | enable_line_input
                | enable_echo_input
            )
            k32.SetConsoleMode(stdin_handle, new_mode)
    except (AttributeError, OSError):
        pass


@contextlib.contextmanager
def terminal_isolated() -> Generator[None, None, None]:
    """终端生命周期隔离上下文管理器（看门狗）。

    在执行子交互、选择器或弹窗前记录终端模式，退出时完整复原并清空缓冲区脏事件。
    """
    saved_mode = get_console_mode()
    try:
        yield
    finally:
        flush_console_input_buffer()
        if saved_mode is not None:
            set_console_mode(saved_mode)
        else:
            restore_console_mode()


def safe_select(
    message: str,
    choices: Sequence[Any],
    *,
    default: Any = None,
    use_shortcuts: bool = False,
) -> Any:
    """执行可取消的选择器；Ctrl+C 或 Esc 返回 None。"""
    with terminal_isolated():
        try:
            import questionary

            question = questionary.select(
                message,
                choices=choices,
                default=default,
                use_shortcuts=use_shortcuts,
            )
            bindings = cast(KeyBindings, question.application.key_bindings)

            @bindings.add("escape", eager=True)
            def _cancel_with_escape(event: Any) -> None:
                """使用 Cade 统一的 Esc 语义关闭选择器。"""
                event.app.exit(result=None, style="class:aborting")

            return question.unsafe_ask()
        except (KeyboardInterrupt, EOFError):
            return None


def safe_checkbox(
    message: str,
    choices: Sequence[Any],
    **kwargs: Any,
) -> list[Any] | None:
    """执行可取消的多选器；Ctrl+C 或 Esc 返回 None。"""
    with terminal_isolated():
        try:
            import questionary

            question = questionary.checkbox(message, choices=choices, **kwargs)
            bindings = cast(KeyBindings, question.application.key_bindings)

            @bindings.add("escape", eager=True)
            def _cancel_with_escape(event: Any) -> None:
                """使用 Cade 统一的 Esc 语义关闭多选器。"""
                event.app.exit(result=None, style="class:aborting")

            return cast(list[Any] | None, question.unsafe_ask())
        except (KeyboardInterrupt, EOFError):
            return None


def safe_text(
    message: str,
    *,
    default: str = "",
    qmark: str = "?",
    **kwargs: Any,
) -> str | None:
    """执行可取消的文本输入；Ctrl+C、Esc 或 EOF 返回 None。"""
    with terminal_isolated():
        try:
            import questionary

            escape_bindings = KeyBindings()

            @escape_bindings.add("escape", eager=True)
            def _cancel_with_escape(event: Any) -> None:
                """使用 Cade 统一的 Esc 语义关闭文本输入。"""
                event.app.exit(result=None, style="class:aborting")

            return questionary.text(
                message,
                default=default,
                qmark=qmark,
                key_bindings=escape_bindings,
                **kwargs,
            ).unsafe_ask()
        except (KeyboardInterrupt, EOFError):
            return None


def suppress_windows_ptk_shutdown_noise() -> None:
    """在 Windows 平台上抑制 prompt_toolkit 与 Win32 句柄退出时的无害 RuntimeError。"""
    global _windows_ptk_patch_installed
    install_force_exit_signal_handler()

    if sys.platform != "win32" or _windows_ptk_patch_installed:
        return
    _windows_ptk_patch_installed = True

    try:
        import prompt_toolkit.eventloop as ptk_eventloop
        import prompt_toolkit.eventloop.utils as ptk_utils

        orig_run = ptk_utils.run_in_executor_with_context

        def _safe_run_in_executor_with_context(
            func: Any,
            *args: Any,
            loop: asyncio.AbstractEventLoop | None = None,
        ) -> Any:
            try:
                target_loop = loop or asyncio.get_running_loop()
                if target_loop.is_closed():
                    return None
                return orig_run(func, *args, loop=target_loop)
            except RuntimeError as exc:
                msg = str(exc)
                if (
                    "Executor shutdown has been called" in msg
                    or "Event loop is closed" in msg
                    or "cannot schedule new futures" in msg
                ):
                    try:
                        target_loop = loop or asyncio.get_running_loop()
                        future = target_loop.create_future()
                        future.cancel()
                        return future
                    except RuntimeError:
                        return None
                raise

        _safe_run_in_executor_with_context._cade_patched = True  # type: ignore[attr-defined]
        ptk_utils.run_in_executor_with_context = _safe_run_in_executor_with_context
        ptk_eventloop.run_in_executor_with_context = _safe_run_in_executor_with_context

        try:
            import prompt_toolkit.input.win32 as ptk_win32

            win32_mod: Any = ptk_win32
            win32_mod.run_in_executor_with_context = _safe_run_in_executor_with_context

            win32_handles_cls = getattr(ptk_win32, "_Win32Handles", None)
            if win32_handles_cls and not getattr(
                win32_handles_cls.add_win32_handle, "_cade_patched", False
            ):
                orig_add = win32_handles_cls.add_win32_handle

                def _safe_add_win32_handle(
                    self: Any, handle: Any, callback: Any
                ) -> None:
                    def _safe_cb() -> None:
                        try:
                            callback()
                        except (RuntimeError, OSError):
                            pass

                    try:
                        return orig_add(self, handle, _safe_cb)
                    except (RuntimeError, OSError):
                        pass

                _safe_add_win32_handle._cade_patched = True  # type: ignore[attr-defined]
                win32_handles_cls.add_win32_handle = _safe_add_win32_handle
        except (ImportError, AttributeError):
            pass
    except (ImportError, AttributeError):
        pass

    def _handler(
        loop: asyncio.AbstractEventLoop,
        context: dict[str, object],
    ) -> None:
        exc = context.get("exception")
        if isinstance(exc, RuntimeError):
            msg = str(exc)
            if (
                "Executor shutdown has been called" in msg
                or "Event loop is closed" in msg
                or "cannot schedule new futures" in msg
            ):
                return
        loop.default_exception_handler(context)

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            policy_cls = type(asyncio.get_event_loop_policy())
        if not getattr(policy_cls.new_event_loop, "_cade_patched", False):
            orig_new_event_loop = policy_cls.new_event_loop

            def _patched_policy_new_event_loop(self: Any) -> asyncio.AbstractEventLoop:
                loop = orig_new_event_loop(self)
                loop.set_exception_handler(_handler)
                return loop

            _patched_policy_new_event_loop._cade_patched = True  # type: ignore[attr-defined]
            policy_cls.new_event_loop = _patched_policy_new_event_loop

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                cur_loop = asyncio.get_event_loop_policy().get_event_loop()
            if cur_loop and not cur_loop.is_closed():
                cur_loop.set_exception_handler(_handler)
        except (RuntimeError, AttributeError):
            pass
    except (RuntimeError, AttributeError):
        pass
