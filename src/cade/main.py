from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import NoReturn

from .coding_agent.app import build_app
from .harness.config import discover_runtime_config, resolve_config_path


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


def _build_config_parser(subparsers) -> None:
    config_parser = subparsers.add_parser(
        "config",
        help="Browse and edit cade settings interactively",
        description=(
            "Open an interactive browser over all cade.config.json settings: "
            "execution modes, agent limits, request hygiene, security policy, "
            "paths, tools and skills. Pick a row to cycle preset values or "
            "type a new one; changes are validated before they are saved."
        ),
    )
    config_parser.add_argument(
        "--project-root", type=Path, default=Path.cwd(), help="Project root directory."
    )
    config_parser.add_argument(
        "--config", type=Path, help="Path to cade.config.json to manage."
    )


def _build_setup_parser(subparsers) -> None:
    subparsers.add_parser("setup", help="Run the provider setup wizard")


def _build_login_parser(subparsers) -> None:
    login_parser = subparsers.add_parser(
        "login",
        aliases=["connect"],
        help="Connect an account or API-key provider",
    )
    _add_auth_arguments(login_parser)


def _build_logout_parser(subparsers) -> None:
    logout_parser = subparsers.add_parser(
        "logout", help="Log out from an AI provider and remove credentials"
    )
    logout_parser.add_argument(
        "--provider",
        default="openai-codex",
        help="Provider to log out from (default: openai-codex).",
    )


def _build_auth_parser(subparsers) -> None:
    auth_parser = subparsers.add_parser(
        "auth", help="Manage authentication credentials"
    )
    auth_subparsers = auth_parser.add_subparsers(dest="auth_action")

    login_p = auth_subparsers.add_parser(
        "login",
        aliases=["connect"],
        help="Connect an account or API-key provider",
    )
    _add_auth_arguments(login_p)

    logout_p = auth_subparsers.add_parser(
        "logout", help="Log out from a provider account"
    )
    logout_p.add_argument(
        "--provider",
        default="openai-codex",
        help="Provider to log out from (default: openai-codex).",
    )

    auth_subparsers.add_parser("status", help="Show current authentication status")


def _build_tui_parser(subparsers) -> None:
    subparsers.add_parser("tui", help="Run the terminal workbench")


def _build_web_parser(subparsers) -> None:
    web_parser = subparsers.add_parser(
        "web",
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
    web_parser.add_argument(
        "--project-root",
        type=Path,
        default=Path.cwd(),
        help="Project root directory.",
    )


def _build_exec_parser(subparsers) -> None:
    from .coding_agent.modes.exec_mode import add_exec_arguments

    exec_parser = subparsers.add_parser(
        "exec",
        help="Run one prompt with a stable automation protocol",
    )
    add_exec_arguments(exec_parser)


def _build_session_parser(subparsers) -> None:
    from .coding_agent.cli.session_cmd import add_session_arguments

    session_parser = subparsers.add_parser(
        "session",
        help="Inspect and control persisted sessions",
    )
    add_session_arguments(session_parser)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    effective_argv = list(sys.argv[1:] if argv is None else argv)
    effective_argv = _normalize_exec_resume(effective_argv)
    parser = argparse.ArgumentParser(description="Cade coding agent.")
    parser.add_argument(
        "--project-root", type=Path, default=Path.cwd(), help="Project root directory."
    )
    parser.add_argument(
        "--config", type=Path, help="Path to cade.config.json runtime settings."
    )
    parser.add_argument(
        "--sessions-dir", type=Path, help="Session transcript directory."
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
        dest="command", parser_class=_CommandArgumentParser
    )
    _build_config_parser(subparsers)
    _build_setup_parser(subparsers)
    _build_login_parser(subparsers)
    _build_logout_parser(subparsers)
    _build_auth_parser(subparsers)
    _build_tui_parser(subparsers)
    _build_exec_parser(subparsers)
    _build_session_parser(subparsers)
    _build_web_parser(subparsers)
    return parser.parse_args(effective_argv)


def _normalize_exec_resume(argv: list[str]) -> list[str]:
    """将 `exec resume ID` 转换为统一的 session 恢复参数。"""
    try:
        exec_index = argv.index("exec")
    except ValueError:
        return argv
    resume_index = exec_index + 1
    session_index = exec_index + 2
    if (
        session_index >= len(argv)
        or argv[resume_index] != "resume"
        or argv[session_index].startswith("-")
    ):
        return argv
    return [
        *argv[:resume_index],
        "--session",
        argv[session_index],
        *argv[session_index + 1 :],
    ]


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
        except RuntimeError as exc:
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
                    open_browser=args.open,
                )
            return _run(args, runtime_config)
        except RuntimeError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 6 if args.command == "exec" else 1

    from .coding_agent.interaction.credentials import has_valid_config

    temp_config: Path | None = None

    if not has_valid_config(project_root):
        if not sys.stdin.isatty():
            print(
                "No credentials configured. Run 'cade login' to sign in with "
                "ChatGPT, or set an API key (e.g. OPENAI_API_KEY, "
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
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        if temp_config is not None and temp_config.exists():
            temp_config.unlink()


def _run(args, runtime_config) -> int:
    if args.command == "exec":
        from .coding_agent.modes.exec_mode import run_exec

        return run_exec(args, runtime_config, _build_app_from_config)

    sessions_dir = (
        args.sessions_dir
        or resolve_config_path(args.project_root, runtime_config.paths.sessions_dir)
        or (args.project_root / ".cade" / "sessions")
    )
    app = _build_app_from_config(args.project_root, runtime_config, sessions_dir)
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
        close = getattr(app, "close", None)
        if callable(close):
            close()


def _build_app_from_config(
    project_root: Path,
    runtime_config,
    sessions_dir: Path,
):
    return build_app(
        project_root=project_root,
        runtime_config=runtime_config,
        sessions_dir=sessions_dir,
    )


if __name__ == "__main__":
    sys.exit(main())
