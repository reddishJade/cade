from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import NoReturn

from .coding_agent.app import build_app
from .harness.config import CadeRuntimeConfig, discover_runtime_config


class _ExplicitAuthMethodAction(argparse.Action):
    """记录用户是否显式指定了认证方式。"""

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: object,
        option_string: str | None = None,
    ) -> None:
        del parser, option_string
        setattr(namespace, self.dest, values)
        namespace._auth_method_explicit = True


class _CommandArgumentParser(argparse.ArgumentParser):
    """Exec 参数错误使用机器协议的稳定退出码。"""

    def error(self, message: str) -> NoReturn:
        if self.prog.endswith(" exec"):
            self.print_usage(sys.stderr)
            self.exit(6, f"{self.prog}: error: {message}\n")
        super().error(message)


def _add_auth_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--provider",
        default="openai-codex",
        help="Account provider for OAuth (default: openai-codex).",
    )
    parser.set_defaults(_auth_method_explicit=False)
    parser.add_argument(
        "--method",
        choices=["browser", "device_code", "api_key"],
        default="browser",
        action=_ExplicitAuthMethodAction,
        help=(
            "Authentication method: browser/device_code for an account, "
            "or api_key (default: prompt interactively)."
        ),
    )


def _build_config_parser(
    subparsers: argparse._SubParsersAction[_CommandArgumentParser],
    workspace_arguments: _CommandArgumentParser,
) -> None:
    subparsers.add_parser(
        "config",
        parents=[workspace_arguments],
        help="Browse and edit cade settings interactively",
        description=(
            "Open an interactive browser over all cade.config.json settings: "
            "execution modes, agent limits, request hygiene, security policy, "
            "paths, tools and skills. Pick a row to cycle preset values or "
            "type a new one; changes are validated before they are saved."
        ),
    )


def _build_setup_parser(
    subparsers: argparse._SubParsersAction[_CommandArgumentParser],
    workspace_arguments: _CommandArgumentParser,
) -> None:
    subparsers.add_parser(
        "setup", parents=[workspace_arguments], help="Run the provider setup wizard"
    )


def _build_login_parser(
    subparsers: argparse._SubParsersAction[_CommandArgumentParser],
    workspace_arguments: _CommandArgumentParser,
) -> None:
    login_parser = subparsers.add_parser(
        "login",
        parents=[workspace_arguments],
        aliases=["connect"],
        help="Connect an account or API-key provider",
    )
    _add_auth_arguments(login_parser)


def _build_logout_parser(
    subparsers: argparse._SubParsersAction[_CommandArgumentParser],
    workspace_arguments: _CommandArgumentParser,
) -> None:
    logout_parser = subparsers.add_parser(
        "logout",
        parents=[workspace_arguments],
        help="Log out from an AI provider and remove credentials",
    )
    logout_parser.add_argument(
        "--provider",
        default="openai-codex",
        help="Provider to log out from (default: openai-codex).",
    )


def _build_auth_parser(
    subparsers: argparse._SubParsersAction[_CommandArgumentParser],
    workspace_arguments: _CommandArgumentParser,
) -> None:
    auth_parser = subparsers.add_parser(
        "auth", parents=[workspace_arguments], help="Manage authentication credentials"
    )
    auth_subparsers = auth_parser.add_subparsers(
        dest="auth_action",
        parser_class=_CommandArgumentParser,
    )

    login_p = auth_subparsers.add_parser(
        "login",
        parents=[workspace_arguments],
        aliases=["connect"],
        help="Connect an account or API-key provider",
    )
    _add_auth_arguments(login_p)

    logout_p = auth_subparsers.add_parser(
        "logout", parents=[workspace_arguments], help="Log out from a provider account"
    )
    logout_p.add_argument(
        "--provider",
        default="openai-codex",
        help="Provider to log out from (default: openai-codex).",
    )

    auth_subparsers.add_parser(
        "status",
        parents=[workspace_arguments],
        help="Show current authentication status",
    )


def _build_tui_parser(
    subparsers: argparse._SubParsersAction[_CommandArgumentParser],
    workspace_arguments: _CommandArgumentParser,
) -> None:
    subparsers.add_parser(
        "tui", parents=[workspace_arguments], help="Run the terminal workbench"
    )


def _build_web_parser(
    subparsers: argparse._SubParsersAction[_CommandArgumentParser],
    workspace_arguments: _CommandArgumentParser,
) -> None:
    web_parser = subparsers.add_parser(
        "web",
        parents=[workspace_arguments],
        help="Run the browser workbench (FastAPI + WebSocket UI)",
        description=(
            "Start the Cade browser workbench: a single-page frontend that "
            "streams agent events over WebSocket."
        ),
    )
    web_parser.add_argument(
        "--host", default="127.0.0.1", help="Bind host address (default: 127.0.0.1)."
    )
    web_parser.add_argument(
        "--port", type=int, default=8787, help="Bind port (default: 8787)."
    )
    web_parser.add_argument(
        "--open",
        action="store_true",
        help="Open the browser automatically after the server starts.",
    )


def _build_exec_parser(
    subparsers: argparse._SubParsersAction[_CommandArgumentParser],
    workspace_arguments: _CommandArgumentParser,
) -> None:
    from .coding_agent.modes.exec_mode import add_exec_arguments

    exec_parser = subparsers.add_parser(
        "exec",
        parents=[workspace_arguments],
        help="Run one prompt with a stable automation protocol",
    )
    add_exec_arguments(exec_parser)


def _build_session_parser(
    subparsers: argparse._SubParsersAction[_CommandArgumentParser],
    workspace_arguments: _CommandArgumentParser,
) -> None:
    from .coding_agent.cli.session_cmd import add_session_arguments

    session_parser = subparsers.add_parser(
        "session",
        parents=[workspace_arguments],
        help="Inspect and control persisted sessions",
    )
    add_session_arguments(session_parser, workspace_arguments)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    workspace_arguments = _CommandArgumentParser(
        add_help=False, argument_default=argparse.SUPPRESS
    )
    workspace_arguments.add_argument(
        "--project-root", type=Path, help="Project root directory (default: cwd)."
    )
    workspace_arguments.add_argument(
        "--config", type=Path, help="Path to cade.config.json runtime settings."
    )
    workspace_arguments.add_argument(
        "--sessions-dir", type=Path, help="Session transcript directory."
    )
    parser = argparse.ArgumentParser(
        description="Cade coding agent.", parents=[workspace_arguments]
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Open the session resume picker on startup.",
    )
    parser.add_argument(
        "-c",
        "--continue",
        action="store_true",
        dest="continue_",
        help="Resume the latest session for the current project.",
    )
    parser.add_argument(
        "--session",
        type=str,
        help="Resume a specific session by id.",
    )
    subparsers = parser.add_subparsers(
        dest="command",
        parser_class=_CommandArgumentParser,
    )
    _build_config_parser(subparsers, workspace_arguments)
    _build_setup_parser(subparsers, workspace_arguments)
    _build_login_parser(subparsers, workspace_arguments)
    _build_logout_parser(subparsers, workspace_arguments)
    _build_auth_parser(subparsers, workspace_arguments)
    _build_tui_parser(subparsers, workspace_arguments)
    _build_exec_parser(subparsers, workspace_arguments)
    _build_session_parser(subparsers, workspace_arguments)
    _build_web_parser(subparsers, workspace_arguments)
    return parser.parse_args(
        argv,
        namespace=argparse.Namespace(
            project_root=Path.cwd(), config=None, sessions_dir=None
        ),
    )


def main() -> int:
    args = parse_args()
    project_root = args.project_root

    if args.command == "config":
        from .coding_agent.cli.config_cmd import handle_config_command

        handle_config_command(args, project_root)
        return 0

    if args.command == "setup":
        from .coding_agent.modes.tui.setup_wizard import run_setup_wizard

        try:
            run_setup_wizard(project_root)
        except KeyboardInterrupt:
            pass
        return 0

    if args.command in {"login", "connect"}:
        from .coding_agent.cli.auth_cmd import handle_login_command

        method = (
            getattr(args, "method", "browser")
            if getattr(args, "_auth_method_explicit", False)
            else None
        )
        return handle_login_command(
            provider=getattr(args, "provider", "openai-codex"),
            method=method,
            project_root=project_root,
        )

    if args.command == "logout":
        from .coding_agent.cli.auth_cmd import handle_logout_command

        return handle_logout_command(
            provider=getattr(args, "provider", "openai-codex"),
        )

    if args.command == "auth":
        from .coding_agent.cli.auth_cmd import (
            handle_login_command,
            handle_logout_command,
            handle_status_command,
        )

        action = getattr(args, "auth_action", None)
        if action in {"login", "connect"}:
            method = (
                getattr(args, "method", "browser")
                if getattr(args, "_auth_method_explicit", False)
                else None
            )
            return handle_login_command(
                provider=getattr(args, "provider", "openai-codex"),
                method=method,
                project_root=project_root,
            )
        if action == "logout":
            return handle_logout_command(
                provider=getattr(args, "provider", "openai-codex"),
            )
        return handle_status_command()

    if args.command == "session":
        from .coding_agent.cli.session_cmd import handle_session_command

        try:
            runtime_config = discover_runtime_config(project_root, args.config)
        except (RuntimeError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 6
        return handle_session_command(args, runtime_config)

    if args.command in {"exec", "web"}:
        try:
            runtime_config = discover_runtime_config(project_root, args.config)
            if args.command == "web":
                from .server.serve import run_web_server

                return run_web_server(
                    project_root,
                    host=args.host,
                    port=args.port,
                    config_path=args.config,
                    runtime_config=runtime_config,
                    sessions_dir=args.sessions_dir,
                    open_browser=args.open,
                )
            return _run(args, runtime_config)
        except (RuntimeError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 6 if args.command == "exec" else 1

    from .coding_agent.interaction.credentials import has_valid_config

    temp_config: Path | None = None

    try:
        configured = has_valid_config(project_root, args.config)
    except (RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if not configured:
        if not sys.stdin.isatty():
            print(
                "No credentials configured. Run 'cade login' to sign in with "
                "ChatGPT and select openai-codex as default_provider, or set an API key (e.g. OPENAI_API_KEY, "
                "DEEPSEEK_API_KEY) in .env or the environment.",
                file=sys.stderr,
            )
            return 1

        from .coding_agent.modes.tui.setup_wizard import (
            prompt_login_method,
            run_setup_wizard,
        )

        try:
            login_method = prompt_login_method()
        except KeyboardInterrupt:
            return 0
        if login_method is None:
            return 0

        if login_method == "auth":
            from .coding_agent.cli.auth_cmd import handle_login_command

            login_status = handle_login_command(
                method="browser",
                project_root=project_root,
            )
            if login_status == 130:
                return 0
            if login_status != 0:
                return login_status
            if not has_valid_config(project_root, args.config):
                print(
                    "Login saved. Select your default provider and model with cade config."
                )
                return 0
        else:
            try:
                status, config_path = run_setup_wizard(project_root)
            except KeyboardInterrupt:
                return 0
            if status == "cancelled":
                return 0
            if status == "no_save" and config_path is not None:
                temp_config = config_path
                args.config = config_path

    try:
        runtime_config = discover_runtime_config(project_root, args.config)
        return _run(args, runtime_config)
    except (RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        if temp_config is not None and temp_config.exists():
            temp_config.unlink()


def _run(args: argparse.Namespace, runtime_config: CadeRuntimeConfig) -> int:
    if args.command == "exec":
        from .coding_agent.modes.exec_mode import run_exec

        return run_exec(args, runtime_config)

    app = build_app(
        args.project_root, runtime_config=runtime_config, sessions_dir=args.sessions_dir
    )
    try:
        from .coding_agent.modes.tui.app import run_tui

        return run_tui(
            app,
            args.project_root,
            session_id=args.session,
            auto_continue=args.continue_,
            resume_latest=args.resume,
            config_path=args.config,
        )
    finally:
        app.close()


if __name__ == "__main__":
    sys.exit(main())
