"""Cade 认证命令处理器。

提供 login、logout 与 status 操作，支持命令行与 REPL 调用。
"""

from __future__ import annotations

import datetime
from pathlib import Path

from cade.harness.auth.manager import AuthManager

from .setup_wizard import prompt_auth_method, run_setup_wizard


def handle_login_command(
    provider: str = "openai-codex",
    method: str | None = None,
    project_root: Path | None = None,
) -> int:
    """处理账户 OAuth 或 API key 配置流程。"""
    if method is None:
        try:
            selected = prompt_auth_method()
        except KeyboardInterrupt:
            print("\nAuthentication method selection cancelled.")
            return 130
        if selected is None:
            return 0
        method = "browser" if selected == "account" else "api_key"

    if method in ("api", "api_key"):
        try:
            run_setup_wizard(project_root or Path.cwd(), from_connect=True)
        except KeyboardInterrupt:
            print("\nAPI key configuration cancelled.")
            return 130
        return 0

    if method == "account":
        method = "browser"

    manager = AuthManager()
    print(f"Starting {provider} authentication login (method: {method})...")

    try:
        if method == "device_code":

            def notify_device(url: str, code: str) -> None:
                print("\n================== Authorization ==================")
                print(f"1. Open in your browser: {url}")
                print(f"2. Enter the one-time device verification code: {code}")
                print("==============================================")
                print(
                    "Waiting for authorization to complete (press Ctrl+C to cancel)..."
                )

            cred = manager.login(
                provider=provider,
                method="device_code",
                notify_callback=notify_device,
            )
        else:

            def notify_browser(msg: str) -> None:
                print(
                    f"\n{msg}\nWaiting for the browser callback (press Ctrl+C to cancel)..."
                )

            cred = manager.login(
                provider=provider,
                method="browser",
                notify_callback=notify_browser,
            )

        print(
            f"\n✓ Successfully logged in to {provider}. Credentials securely saved to {manager.storage_location}"
        )
        if cred.account_id:
            print(f"  Account ID: {cred.account_id}")
        if cred.expires:
            exp_dt = datetime.datetime.fromtimestamp(
                cred.expires, tz=datetime.UTC
            ).astimezone()
            print(f"  Access token expires at: {exp_dt.strftime('%Y-%m-%d %H:%M:%S')}")
        return 0
    except KeyboardInterrupt:
        print("\nLogin cancelled.")
        return 130
    except (RuntimeError, TimeoutError, ValueError) as exc:
        print(f"\n✗ Login failed: {exc}")
        return 1


def handle_logout_command(provider: str = "openai-codex") -> int:
    """处理用户登出流程并清除本地凭据。"""
    manager = AuthManager()
    success = manager.logout(provider)
    if success:
        print(f"✓ Successfully logged out of {provider}; local credentials cleared.")
    else:
        print(f"No local credentials found for {provider}; not currently logged in.")
    return 0


def handle_status_command() -> int:
    """打印当前所有账号认证状态。"""
    manager = AuthManager()
    accounts = manager.list_accounts()
    if not accounts:
        print(
            f"No accounts are currently logged in. Credential file: {manager.storage_location}"
        )
        print("You can use `cade login` to sign in.")
        return 0

    print(f"Logged-in accounts (stored at {manager.storage_location}):")
    for acc in accounts:
        provider = str(acc.get("provider", ""))
        account_id = str(acc.get("account_id") or "default")
        expired = bool(acc.get("expired", False))
        has_refresh = bool(acc.get("has_refresh", False))
        status_text = "expired" if expired else "valid"
        if expired and has_refresh:
            status_text = "expired (refresh available)"
        print(
            f"  • Provider: {provider} | Account: {account_id} | Status: {status_text}"
        )
    return 0
