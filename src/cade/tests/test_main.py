from types import SimpleNamespace

from cade.main import _run, main, parse_args


def _patch_main_startup(
    monkeypatch,
    *,
    isatty: bool,
    login_method: str | None = None,
) -> None:
    """隔离 main() 启动路径的外部依赖。"""
    monkeypatch.setattr("cade.main.parse_args", lambda argv=None: parse_args([]))
    monkeypatch.setattr(
        "cade.cli.ptk_patch.suppress_windows_ptk_shutdown_noise", lambda: None
    )
    monkeypatch.setattr("cade.main.has_valid_config", lambda root: False)
    monkeypatch.setattr("cade.main.sys.stdin", SimpleNamespace(isatty=lambda: isatty))
    monkeypatch.setattr("cade.main.prompt_login_method", lambda: login_method)
    monkeypatch.setattr(
        "cade.main.discover_runtime_config",
        lambda *args, **kwargs: SimpleNamespace(
            paths=SimpleNamespace(sessions_dir=None)
        ),
    )
    monkeypatch.setattr("cade.main._run", lambda *args, **kwargs: 0)


def test_parse_args_supports_tui_and_cli_commands() -> None:
    assert parse_args(["tui"]).command == "tui"
    assert parse_args(["cli"]).command == "cli"


def test_default_command_is_tui(monkeypatch) -> None:
    calls: list[str] = []

    monkeypatch.setattr("cade.main._build_app_from_config", lambda *_: object())
    monkeypatch.setattr(
        "cade.main.run_tui", lambda *args, **kwargs: calls.append("tui") or 0
    )
    monkeypatch.setattr(
        "cade.main.run_repl", lambda *args, **kwargs: calls.append("cli") or 0
    )

    args = parse_args([])
    runtime_config = SimpleNamespace(paths=SimpleNamespace(sessions_dir=None))

    assert _run(args, runtime_config) == 0
    assert calls == ["tui"]


def test_cli_command_starts_repl(monkeypatch) -> None:
    calls: list[str] = []

    monkeypatch.setattr("cade.main._build_app_from_config", lambda *_: object())
    monkeypatch.setattr(
        "cade.main.run_tui", lambda *args, **kwargs: calls.append("tui") or 0
    )
    monkeypatch.setattr(
        "cade.main.run_repl", lambda *args, **kwargs: calls.append("cli") or 0
    )

    args = parse_args(["cli"])
    runtime_config = SimpleNamespace(paths=SimpleNamespace(sessions_dir=None))

    assert _run(args, runtime_config) == 0
    assert calls == ["cli"]


def test_main_requires_credentials_in_non_tty(monkeypatch, capsys) -> None:
    _patch_main_startup(monkeypatch, isatty=False)

    assert main() == 1
    assert "cade login" in capsys.readouterr().err


def test_main_starts_oauth_login_when_choosing_auth(monkeypatch) -> None:
    calls: list[str] = []
    _patch_main_startup(monkeypatch, isatty=True, login_method="auth")
    monkeypatch.setattr(
        "cade.cli.auth_cmd.handle_login_command",
        lambda **kwargs: calls.append("login") or 0,
    )

    assert main() == 0
    assert calls == ["login"]


def test_main_aborts_when_oauth_login_fails(monkeypatch) -> None:
    _patch_main_startup(monkeypatch, isatty=True, login_method="auth")
    monkeypatch.setattr("cade.cli.auth_cmd.handle_login_command", lambda **kwargs: 1)

    assert main() == 1


def test_main_runs_setup_wizard_when_choosing_api(monkeypatch) -> None:
    calls: list[str] = []
    _patch_main_startup(monkeypatch, isatty=True, login_method="api")
    monkeypatch.setattr(
        "cade.main.run_setup_wizard",
        lambda root: calls.append("wizard") or ("saved", None),
    )

    assert main() == 0
    assert calls == ["wizard"]


def test_main_returns_zero_when_login_choice_cancelled(monkeypatch) -> None:
    _patch_main_startup(monkeypatch, isatty=True, login_method=None)

    assert main() == 0
