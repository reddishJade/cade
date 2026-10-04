from __future__ import annotations

from cade.coding_agent.interaction.commands import CommandContext

from ...cli.auth_cmd import (
    handle_login_command,
    handle_logout_command,
    handle_status_command,
)
from .config_registry import (
    edit_setting_interactive,
    find_setting,
    load_effective_config,
    matching_settings,
    run_config_browser,
)
from .settings import (
    handle_effort_command,
    handle_model_command,
    handle_permissions,
    handle_thinking_command,
)
from .setup_wizard import CONFIG_FILENAME


def cmd_login(cmd: str, ctx: CommandContext) -> bool:
    """选择账户 OAuth 或 API key，连接一个 AI 提供方。"""

    parts = cmd.split()
    method: str | None = None
    provider = "openai-codex"
    for part in parts[1:]:
        if part in ("device", "device_code", "--device", "-d"):
            method = "device_code"
        elif part in ("account", "browser"):
            method = "browser"
        elif part in ("api", "api_key"):
            method = "api_key"
        elif not part.startswith("-"):
            provider = part

    handle_login_command(
        provider=provider,
        method=method,
        project_root=ctx.project_root,
    )
    return False


def cmd_logout(cmd: str, ctx: CommandContext) -> bool:
    """登出 AI 提供方账号并清除本地凭据。"""

    parts = cmd.split()
    provider = (
        parts[1] if len(parts) > 1 and not parts[1].startswith("-") else "openai-codex"
    )
    handle_logout_command(provider=provider)
    return False


def cmd_auth(cmd: str, ctx: CommandContext) -> bool:
    """显示认证状态或执行登录/登出。"""

    parts = cmd.split()
    subcmd = parts[1].lower() if len(parts) > 1 else "status"
    if subcmd in {"login", "connect"}:
        return cmd_login(" ".join(parts[1:]), ctx)
    if subcmd == "logout":
        return cmd_logout(" ".join(parts[1:]), ctx)
    if subcmd not in {"status", "list"} or len(parts) > 2:
        ctx.output.write("Usage: /auth [status|list|login|connect|logout]")
        return False
    handle_status_command()
    return False


def cmd_model(cmd: str, ctx: CommandContext) -> bool:
    """显示或切换当前模型。"""
    handle_model_command(cmd, ctx.app)
    return False


def cmd_effort(cmd: str, ctx: CommandContext) -> bool:
    """显示或设置 reasoning effort 级别。"""
    handle_effort_command(cmd, ctx.app)
    return False


def cmd_thinking(cmd: str, ctx: CommandContext) -> bool:
    """显示或切换推理摘要及协议对应的思考开关。"""
    handle_thinking_command(cmd, ctx.app)
    return False


def cmd_config(cmd: str, ctx: CommandContext) -> bool:
    """打开交互式配置浏览器，浏览并修改 cade.config.json。"""
    config_path = ctx.project_root / CONFIG_FILENAME
    parts = cmd.split(maxsplit=1)
    query = parts[1].strip() if len(parts) > 1 else ""

    if not query:
        run_config_browser(config_path)
        return False

    spec = find_setting(query)
    if spec is None:
        matches = matching_settings(query)
        if matches:
            print(f"'{query}' is ambiguous. Did you mean:")
            for match in matches:
                print(f"  {match.label} ({match.key})")
        else:
            print(f"No setting matches '{query}'. Use '/config' to browse all.")
        return False

    edit_setting_interactive(config_path, spec, load_effective_config(config_path))
    return False


def cmd_permissions(cmd: str, ctx: CommandContext) -> bool:
    """列出或清除权限规则。"""
    handle_permissions(
        cmd,
        ctx.session_grant_store,
        ctx.permanent_grant_store,
        static_policy=ctx.static_policy,
        restricted_dirs=ctx.restricted_dirs,
        project_root=ctx.project_root,
        app=ctx.app,
        store=ctx.store,
    )
    return False


HOST_COMMANDS = {
    "/login": cmd_login,
    "/connect": cmd_login,
    "/logout": cmd_logout,
    "/auth": cmd_auth,
    "/model": cmd_model,
    "/effort": cmd_effort,
    "/thinking": cmd_thinking,
    "/config": cmd_config,
    "/permissions": cmd_permissions,
}


def run_host_command(command: str, ctx: CommandContext) -> bool:
    return HOST_COMMANDS[command.split(maxsplit=1)[0]](command, ctx)
