from pathlib import Path
from types import SimpleNamespace

import pytest

from cade.agent.results import TerminationReason
from cade.main import _print_stream, _run, main, parse_args


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


def test_session_command_does_not_require_provider_credentials(
    monkeypatch, tmp_path: Path
) -> None:
    calls: list[str] = []
    args = parse_args(
        [
            "session",
            "--project-root",
            str(tmp_path),
            "status",
            "run-1",
        ]
    )
    monkeypatch.setattr("cade.main.parse_args", lambda: args)
    monkeypatch.setattr(
        "cade.cli.ptk_patch.suppress_windows_ptk_shutdown_noise", lambda: None
    )
    monkeypatch.setattr(
        "cade.main.discover_runtime_config",
        lambda *_args: calls.append("config") or object(),
    )
    monkeypatch.setattr(
        "cade.cli.session_cmd.handle_session_command",
        lambda *_args: calls.append("session") or 0,
    )
    monkeypatch.setattr(
        "cade.main.has_valid_config",
        lambda _root: (_ for _ in ()).throw(AssertionError("credentials checked")),
    )

    assert main() == 0
    assert calls == ["config", "session"]


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


@pytest.mark.parametrize("command", ["tui", "cli"])
def test_interactive_entry_closes_app_once_when_host_fails(
    monkeypatch,
    command: str,
) -> None:
    calls: list[str] = []

    class _App:
        def close(self) -> None:
            calls.append("close")

    def fail(*_args: object, **_kwargs: object) -> int:
        raise RuntimeError("host failed")

    monkeypatch.setattr("cade.main._build_app_from_config", lambda *_: _App())
    monkeypatch.setattr("cade.main.run_tui", fail)
    monkeypatch.setattr("cade.main.run_repl", fail)
    args = parse_args([command])
    runtime_config = SimpleNamespace(paths=SimpleNamespace(sessions_dir=None))

    with pytest.raises(RuntimeError, match="host failed"):
        _run(args, runtime_config)

    assert calls == ["close"]


def test_single_shot_restores_requested_session_and_closes_app(
    monkeypatch, tmp_path: Path
) -> None:
    calls: list[object] = []
    view = SimpleNamespace(id="session-1", project_path=str(tmp_path))

    class _Store:
        def find_by_id(self, session_id: str):
            calls.append(("find", session_id))
            return view

        def resume(self, session_id: str) -> None:
            calls.append(("resume", session_id))

    class _App:
        session_store = _Store()

        def restore_session(self) -> None:
            calls.append("restore")

        def ask_stream(self, prompt: str):
            calls.append(("ask", prompt))
            return iter(())

        def close(self) -> None:
            calls.append("close")

    monkeypatch.setattr("cade.main._build_app_from_config", lambda *_: _App())
    args = parse_args(
        [
            "--project-root",
            str(tmp_path),
            "--session",
            "session-1",
            "-p",
            "continue",
        ]
    )
    runtime_config = SimpleNamespace(paths=SimpleNamespace(sessions_dir=None))

    assert _run(args, runtime_config) == 0
    assert calls == [
        ("find", "session-1"),
        ("resume", "session-1"),
        "restore",
        ("ask", "continue"),
        "close",
    ]


def test_single_shot_closes_app_when_session_is_missing(
    monkeypatch, tmp_path: Path
) -> None:
    calls: list[str] = []

    class _Store:
        def find_by_id(self, _session_id: str):
            return None

    app = SimpleNamespace(
        session_store=_Store(),
        close=lambda: calls.append("close"),
    )
    monkeypatch.setattr("cade.main._build_app_from_config", lambda *_: app)
    args = parse_args(
        [
            "--project-root",
            str(tmp_path),
            "--session",
            "missing",
            "-p",
            "continue",
        ]
    )
    runtime_config = SimpleNamespace(paths=SimpleNamespace(sessions_dir=None))

    try:
        _run(args, runtime_config)
    except RuntimeError as exc:
        assert str(exc) == "Session not found: missing"
    else:
        raise AssertionError("missing session should fail")
    assert calls == ["close"]


def test_single_shot_prints_non_completed_stop_reason(capsys) -> None:
    _print_stream(
        iter(
            [
                SimpleNamespace(type="text_delta", data="working"),
                SimpleNamespace(
                    type="final",
                    data=SimpleNamespace(
                        answer="working",
                        termination_reason=TerminationReason.STEP_LIMIT,
                        watchdog_reason=None,
                        error_detail=None,
                    ),
                ),
            ]
        )
    )

    assert capsys.readouterr().out == "working\n[stopped] step limit reached\n"


def test_single_shot_does_not_duplicate_step_limit_fallback(capsys) -> None:
    _print_stream(
        iter(
            [
                SimpleNamespace(
                    type="final",
                    data=SimpleNamespace(
                        answer="step limit reached",
                        termination_reason=TerminationReason.STEP_LIMIT,
                        watchdog_reason=None,
                        error_detail=None,
                    ),
                )
            ]
        )
    )

    assert capsys.readouterr().out == "[stopped] step limit reached\n"


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
