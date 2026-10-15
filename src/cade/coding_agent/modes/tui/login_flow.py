"""TUI 内的 /login 流程。

输入全部使用 TUI 的选择菜单与单行文本框；账户授权在宿主提供的后台任务中等待，
由宿主持有的取消事件结束。命令行的 `cade login` 使用 cli/auth_cmd.py 的终端向导，
两者只共享保存连接与授权的实现。
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import httpx

from cade.ai.auth import AuthCredential, LoginCancelledError
from cade.harness.auth.manager import AuthManager

from ...cli.auth_cmd import login_success_lines
from .setup_wizard import (
    AUTH_METHOD_ACCOUNT,
    AUTH_METHOD_API_KEY,
    PROVIDER_PRESETS,
    default_base_url,
    env_api_key,
    save_api_key_connection,
)

DEFAULT_LOGIN_PROVIDER = "openai-codex"

OpenChoices = Callable[[Sequence[tuple[str, object]], Callable[[object], None]], None]
OpenText = Callable[[str, Callable[[str], None]], None]
PostMessage = Callable[[str, str], None]
LoginTask = Callable[[threading.Event], None]
RunTask = Callable[[LoginTask], None]


@dataclass(frozen=True)
class LoginRequest:
    """一次 /login 调用的参数；method 为 None 时先询问认证方式。"""

    provider: str = DEFAULT_LOGIN_PROVIDER
    method: str | None = None


def parse_login_command(text: str) -> LoginRequest | None:
    """解析 /login、/connect 与 /auth login|connect；其他命令返回 None。"""
    parts = text.split()
    if not parts:
        return None
    if parts[0] == "/auth":
        if len(parts) < 2 or parts[1].lower() not in {"login", "connect"}:
            return None
        args = parts[2:]
    elif parts[0] in {"/login", "/connect"}:
        args = parts[1:]
    else:
        return None

    provider = DEFAULT_LOGIN_PROVIDER
    method: str | None = None
    for part in args:
        if part in ("device", "device_code", "--device", "-d"):
            method = "device_code"
        elif part in ("account", "browser"):
            method = "browser"
        elif part in ("api", "api_key"):
            method = "api_key"
        elif not part.startswith("-"):
            provider = part
    return LoginRequest(provider, method)


class TuiLoginFlow:
    """用 TUI 原生控件完成登录。

    open_choices、open_text 在 UI 线程打开菜单和文本框，Esc 关闭即取消；
    post 线程安全地追加一条界面消息；run_task 在后台线程运行授权任务，
    并在用户取消时设置传入的事件。
    """

    def __init__(
        self,
        *,
        open_choices: OpenChoices,
        open_text: OpenText,
        post: PostMessage,
        run_task: RunTask,
    ) -> None:
        self._open_choices = open_choices
        self._open_text = open_text
        self._post = post
        self._run_task = run_task

    def start(self, request: LoginRequest) -> None:
        """按请求进入认证方式选择、API key 表单或账户授权。"""
        if request.method is None:
            self._open_choices(
                [(AUTH_METHOD_ACCOUNT, "browser"), (AUTH_METHOD_API_KEY, "api_key")],
                lambda method: self.start(LoginRequest(request.provider, str(method))),
            )
            return
        if request.method == "api_key":
            self._choose_api_provider()
            return
        self._start_account_login(request.provider, request.method)

    # ── API 密钥 ──

    def _choose_api_provider(self) -> None:
        self._open_choices(
            [(preset["label"], key) for key, preset in PROVIDER_PRESETS.items()],
            lambda key: self._ask_api_key(str(key)),
        )

    def _ask_api_key(self, provider_key: str) -> None:
        preset = PROVIDER_PRESETS[provider_key]
        env_value = env_api_key(preset)
        keep_env = f"; enter keeps ${preset['env_key']}" if env_value else ""
        self._open_text(
            f"{preset['label']} API key (esc to cancel{keep_env})",
            lambda value: self._ask_base_url(provider_key, value.strip() or env_value),
        )

    def _ask_base_url(self, provider_key: str, api_key: str) -> None:
        preset = PROVIDER_PRESETS[provider_key]
        if not api_key:
            self._post("error", f"No API key entered for {preset['label']}.")
            return
        default = default_base_url(preset)
        keep_default = f"; enter keeps {default}" if default else ""
        self._open_text(
            f"Base URL (esc to cancel{keep_default})",
            lambda value: self._save_api_key(
                provider_key, api_key, value.strip() or default
            ),
        )

    def _save_api_key(self, provider_key: str, api_key: str, base_url: str) -> None:
        label = PROVIDER_PRESETS[provider_key]["label"]
        try:
            path = save_api_key_connection(provider_key, api_key, base_url)
        except (OSError, ValueError) as exc:
            self._post("error", f"Failed to save credentials for {label}: {exc}")
            return
        self._post(
            "system",
            f"✓ Credentials saved for {label} ({path}). Default model unchanged.",
        )

    # ── 账户授权 ──

    def _start_account_login(self, provider: str, method: str) -> None:
        notify = self._notify_device if method == "device_code" else self._notify_url

        def work(cancel: threading.Event) -> None:
            manager = AuthManager()
            self._post("system", f"Starting {provider} sign-in ({method})...")
            try:
                credential = manager.login(
                    provider,
                    method=method,
                    notify_callback=notify,
                    cancel_event=cancel,
                )
            except LoginCancelledError:
                self._post("system", "Login cancelled.")
                return
            except (
                OSError,
                RuntimeError,
                TimeoutError,
                ValueError,
                httpx.HTTPError,
            ) as exc:
                self._post("error", f"✗ Login failed: {exc}")
                return
            self._post_success(provider, credential, manager.storage_location)

        self._run_task(work)

    def _notify_url(self, message: str) -> None:
        self._post(
            "system",
            f"{message}\nWaiting for the browser callback (Ctrl+C to cancel)...",
        )

    def _notify_device(self, url: str, code: str) -> None:
        self._post(
            "system",
            f"1. Open in your browser: {url}\n"
            f"2. Enter the one-time device verification code: {code}\n"
            "Waiting for authorization (Ctrl+C to cancel)...",
        )

    def _post_success(
        self, provider: str, credential: AuthCredential, location: str
    ) -> None:
        self._post(
            "system", "\n".join(login_success_lines(provider, credential, location))
        )
