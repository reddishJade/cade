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
            print("\n认证方式选择已取消。")
            return 130
        if selected is None:
            return 0
        method = "browser" if selected == "account" else "api_key"

    if method in ("api", "api_key"):
        try:
            run_setup_wizard(project_root or Path.cwd(), from_connect=True)
        except KeyboardInterrupt:
            print("\nAPI key 配置已取消。")
            return 130
        return 0

    if method == "account":
        method = "browser"

    manager = AuthManager()
    print(f"正在启动 {provider} 认证登录 (模式: {method})...")

    try:
        if method == "device_code":

            def notify_device(url: str, code: str) -> None:
                print("\n================== 授权提示 ==================")
                print(f"1. 请在浏览器中打开: {url}")
                print(f"2. 输入一次性设备验证码: {code}")
                print("==============================================")
                print("正在等待授权完成 (按 Ctrl+C 取消)...")

            cred = manager.login(
                provider=provider,
                method="device_code",
                notify_callback=notify_device,
            )
        else:

            def notify_browser(msg: str) -> None:
                print(f"\n{msg}\n正在等待浏览器回调 (按 Ctrl+C 取消)...")

            cred = manager.login(
                provider=provider,
                method="browser",
                notify_callback=notify_browser,
            )

        print(f"\n✓ 成功登录 {provider}！凭据已安全保存至 {manager.storage_location}")
        if cred.account_id:
            print(f"  账号 ID: {cred.account_id}")
        if cred.expires:
            exp_dt = datetime.datetime.fromtimestamp(
                cred.expires, tz=datetime.UTC
            ).astimezone()
            print(f"  访问令牌有效期至: {exp_dt.strftime('%Y-%m-%d %H:%M:%S')}")
        return 0
    except KeyboardInterrupt:
        print("\n登录操作已取消。")
        return 130
    except (RuntimeError, TimeoutError, ValueError) as exc:
        print(f"\n✗ 登录失败: {exc}")
        return 1


def handle_logout_command(provider: str = "openai-codex") -> int:
    """处理用户登出流程并清除本地凭据。"""
    manager = AuthManager()
    success = manager.logout(provider)
    if success:
        print(f"✓ 已成功登出 {provider}，本地凭据已清除。")
    else:
        print(f"未找到 {provider} 的本地凭据或当前未登录。")
    return 0


def handle_status_command() -> int:
    """打印当前所有账号认证状态。"""
    manager = AuthManager()
    accounts = manager.list_accounts()
    if not accounts:
        print(f"当前暂无已登录账号。凭据文件: {manager.storage_location}")
        print("您可以使用 `cade login` 进行登录。")
        return 0

    print(f"已登录账号列表 (存储于 {manager.storage_location}):")
    for acc in accounts:
        provider = str(acc.get("provider", ""))
        account_id = str(acc.get("account_id") or "默认")
        expired = bool(acc.get("expired", False))
        has_refresh = bool(acc.get("has_refresh", False))
        status_text = "已过期" if expired else "有效"
        if expired and has_refresh:
            status_text = "已过期 (支持自动刷新)"
        print(f"  • Provider: {provider} | Account: {account_id} | 状态: {status_text}")
    return 0
