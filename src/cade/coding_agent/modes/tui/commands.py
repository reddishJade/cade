from __future__ import annotations

from cade.coding_agent.interaction.commands import CommandContext

from ...cli.auth_cmd import (
    handle_logout_command,
    handle_status_command,
)
from .settings import (
    handle_context_window_command,
    handle_effort_command,
    handle_model_command,
    handle_permissions,
    handle_thinking_command,
)


def cmd_logout(cmd: str, ctx: CommandContext) -> bool:
    """登出 AI 提供方账号并清除本地凭据。"""

    parts = cmd.split()
    provider = (
        parts[1] if len(parts) > 1 and not parts[1].startswith("-") else "openai-codex"
    )
    handle_logout_command(provider=provider)
    return False


def cmd_auth(cmd: str, ctx: CommandContext) -> bool:
    """显示认证状态或登出；/auth login|connect 由 TUI 登录流程处理。"""

    parts = cmd.split()
    subcmd = parts[1].lower() if len(parts) > 1 else "status"
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


def cmd_context_window(cmd: str, ctx: CommandContext) -> bool:
    """显示或设置当前会话的上下文窗口大小。"""
    handle_context_window_command(cmd, ctx.app, ctx.project_root)
    return False


def cmd_thinking(cmd: str, ctx: CommandContext) -> bool:
    """显示或切换推理摘要及协议对应的思考开关。"""
    handle_thinking_command(cmd, ctx.app)
    return False


def cmd_permissions(cmd: str, ctx: CommandContext) -> bool:
    """列出或清除权限规则。"""
    handle_permissions(
        cmd,
        ctx.session_grant_store,
        ctx.permanent_grant_store,
        static_policy=ctx.static_policy,
        restricted_dirs=ctx.restricted_dirs,
        app=ctx.app,
    )
    return False


HOST_COMMANDS = {
    "/logout": cmd_logout,
    "/auth": cmd_auth,
    "/model": cmd_model,
    "/effort": cmd_effort,
    "/context-window": cmd_context_window,
    "/thinking": cmd_thinking,
    "/permissions": cmd_permissions,
}


def run_host_command(command: str, ctx: CommandContext) -> bool:
    """执行宿主命令；需要界面交互的命令（/login、/config 等）由 TUI 在分发前处理。"""
    handler = HOST_COMMANDS.get(command.split(maxsplit=1)[0])
    if handler is None:
        ctx.output.write(f"Command is handled by the TUI: {command}")
        return False
    return handler(command, ctx)
