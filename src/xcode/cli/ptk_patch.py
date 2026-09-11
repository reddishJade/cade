"""Windows prompt_toolkit 退出及异步执行器噪声抑制与强制退出处理器。"""

from __future__ import annotations

import asyncio
import sys
import time
import warnings
from typing import Any

_last_sigint_time: float = 0.0


def install_force_exit_signal_handler() -> None:
    """安装底层 SIGINT 信号监听器：任何情况下 3 秒内连续两次 Ctrl+C 强制退出进程。"""
    import signal

    orig_handler = signal.getsignal(signal.SIGINT)

    def _sigint_handler(signum: int, frame: Any) -> None:
        global _last_sigint_time
        now = time.monotonic()
        if _last_sigint_time > 0 and (now - _last_sigint_time) < 3.0:
            sys.stderr.write(
                "\n\033[91m[强制退出]\033[0m 检测到连续 Ctrl+C，正在终止 Xcode...\n"
            )
            sys.stderr.flush()
            import os

            os._exit(0)
        _last_sigint_time = now
        if callable(orig_handler):
            orig_handler(signum, frame)
        else:
            raise KeyboardInterrupt()

    try:
        signal.signal(signal.SIGINT, _sigint_handler)
    except (ValueError, OSError):
        pass


def restore_console_mode() -> None:
    """确保 Windows 控制台模式恢复正常，允许正常接收与分发键盘信号。"""
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


def suppress_windows_ptk_shutdown_noise() -> None:
    """在 Windows 平台上抑制 prompt_toolkit 与 Win32 句柄退出时的无害 RuntimeError。"""
    install_force_exit_signal_handler()

    if sys.platform != "win32":
        return

    # 1. 对 prompt_toolkit 的 run_in_executor_with_context 进行多模块防弹包装
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
                ):
                    try:
                        target_loop = loop or asyncio.get_running_loop()
                        future = target_loop.create_future()
                        future.cancel()
                        return future
                    except RuntimeError:
                        return None
                raise

        _safe_run_in_executor_with_context._xcode_patched = True  # type: ignore[attr-defined]
        ptk_utils.run_in_executor_with_context = _safe_run_in_executor_with_context
        ptk_eventloop.run_in_executor_with_context = _safe_run_in_executor_with_context

        try:
            import prompt_toolkit.input.win32 as ptk_win32

            win32_mod: Any = ptk_win32
            win32_mod.run_in_executor_with_context = _safe_run_in_executor_with_context

            # 包装 _Win32Handles.add_win32_handle，防止 ready() 抛出异常中断事件循环
            win32_handles_cls = getattr(ptk_win32, "_Win32Handles", None)
            if win32_handles_cls and not getattr(
                win32_handles_cls.add_win32_handle, "_xcode_patched", False
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

                    return orig_add(self, handle, _safe_cb)

                _safe_add_win32_handle._xcode_patched = True  # type: ignore[attr-defined]
                win32_handles_cls.add_win32_handle = _safe_add_win32_handle
        except (ImportError, AttributeError):
            pass
    except (ImportError, AttributeError):
        pass

    # 2. 对 asyncio 事件循环异常处理器进行抑制补丁
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
            ):
                return
        loop.default_exception_handler(context)

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            policy_cls = type(asyncio.get_event_loop_policy())
        if not getattr(policy_cls.new_event_loop, "_xcode_patched", False):
            orig_new_event_loop = policy_cls.new_event_loop

            def _patched_policy_new_event_loop(self: Any) -> asyncio.AbstractEventLoop:
                loop = orig_new_event_loop(self)
                loop.set_exception_handler(_handler)
                return loop

            _patched_policy_new_event_loop._xcode_patched = True  # type: ignore[attr-defined]
            policy_cls.new_event_loop = _patched_policy_new_event_loop

        # 也对当前现有 loop 安装异常处理器
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
