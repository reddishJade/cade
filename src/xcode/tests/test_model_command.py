"""测试 REPL /model 命令切换逻辑。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from xcode.cli.repl_settings import handle_effort_command, handle_model_command


class DummyApp:
    def __init__(self) -> None:
        self.current_model = "current-model"
        self.current_transport = "deepseek_chat"
        self.calls: list[dict[str, Any]] = []

    def get_model_info(self) -> dict[str, str]:
        return {
            "model": self.current_model,
            "transport": self.current_transport,
            "base_url": "https://api.deepseek.com",
        }

    def set_model(
        self,
        *,
        model: str,
        profile: str = "main",
        transport: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        account_id: str | None = None,
        thinking: bool | None = None,
        reasoning_effort: str | None = None,
    ) -> str:
        self.calls.append(
            {
                "model": model,
                "profile": profile,
                "transport": transport,
                "base_url": base_url,
                "api_key": api_key,
                "account_id": account_id,
                "thinking": thinking,
                "reasoning_effort": reasoning_effort,
            }
        )
        self.current_model = model
        if transport:
            self.current_transport = transport
        return model


def test_handle_model_command_no_args_non_tty(capsys: Any) -> None:
    app = DummyApp()
    with patch("sys.stdin.isatty", return_value=False):
        handle_model_command("/model", app)
    captured = capsys.readouterr().out
    assert "Model    : current-model" in captured
    assert "用法: /model" in captured


def test_handle_model_command_codex_alias() -> None:
    from xcode.ai.resolver import ModelResolver

    app = DummyApp()
    handle_model_command("/model codex", app)
    assert len(app.calls) == 1
    call = app.calls[0]
    assert call["model"] == ModelResolver.resolve_alias("codex")
    assert call["transport"] == "openai_codex"
    assert call["profile"] == "main"


def test_handle_model_command_openai_codex_alias() -> None:
    from xcode.ai.resolver import ModelResolver

    app = DummyApp()
    handle_model_command("/model openai-codex", app)
    assert len(app.calls) == 1
    call = app.calls[0]
    assert call["model"] == ModelResolver.resolve_alias("openai-codex")
    assert call["transport"] == "openai_codex"


def test_handle_model_command_provider_prefix() -> None:
    app = DummyApp()
    handle_model_command("/model deepseek/model-under-test", app)
    assert len(app.calls) == 1
    call = app.calls[0]
    assert call["model"] == "model-under-test"
    assert call["transport"] == "deepseek_chat"


def test_handle_model_command_thinking_level() -> None:
    app = DummyApp()
    handle_model_command("/model model-under-test:high", app)
    assert len(app.calls) == 1
    call = app.calls[0]
    assert call["model"] == "model-under-test"
    assert call["thinking"] is True
    assert call["reasoning_effort"] == "high"


def test_handle_model_command_thinking_flag() -> None:
    app = DummyApp()
    handle_model_command("/model model-under-test --thinking off", app)
    assert len(app.calls) == 1
    call = app.calls[0]
    assert call["model"] == "model-under-test"
    assert call["thinking"] is False
    assert call["reasoning_effort"] is None


def test_handle_model_command_rejects_unsupported_effort(capsys: Any) -> None:
    app = DummyApp()
    handle_model_command("/model gpt-5.6-luna:minimal", app)

    assert app.calls == []
    output = capsys.readouterr().out
    assert "Invalid effort level for gpt-5.6-luna" in output
    assert "none/low/medium/high/xhigh/max" in output


def test_handle_effort_command_uses_current_model_capabilities(capsys: Any) -> None:
    app = DummyApp()
    app.current_model = "gpt-5.6-luna"
    app.current_transport = "openai_codex"

    handle_effort_command("/effort minimal", app)
    assert app.calls == []
    output = capsys.readouterr().out
    assert "Invalid effort level" in output
    assert "none/low/medium/high/xhigh/max" in output

    handle_effort_command("/effort max", app)
    assert app.calls[-1]["reasoning_effort"] == "max"


def test_handle_model_command_interactive_select() -> None:
    from xcode.ai.models import get_codex_models

    app = DummyApp()
    selected_model = get_codex_models()[0].id
    with (
        patch("sys.stdin.isatty", return_value=True),
        patch(
            "xcode.harness.auth.manager.AuthManager.get_valid_credential",
            return_value=type(
                "Cred",
                (),
                {"access": "tok-123", "account_id": "acc-123", "expires": None},
            )(),
        ),
        patch("questionary.select") as mock_select,
    ):
        mock_select.return_value.ask.return_value = (selected_model, "openai_codex")
        handle_model_command("/model", app)

    assert len(app.calls) == 1
    call = app.calls[0]
    assert call["model"] == selected_model
    assert call["transport"] == "openai_codex"


def test_xcode_app_set_model_smart_inference() -> None:
    from unittest.mock import MagicMock

    from xcode.ai.providers.registry import ModelProfileConfig
    from xcode.ai.resolver import ModelResolver
    from xcode.coding_agent.app import XcodeApp

    mock_agent = MagicMock()
    app = XcodeApp(
        agent=mock_agent,
        _model_profiles={"main": ModelProfileConfig(transport="deepseek_chat")},
    )

    with patch(
        "xcode.harness.auth.manager.AuthManager.get_valid_credential",
        return_value=type(
            "Cred",
            (),
            {"access": "tok-chatgpt", "account_id": "acc-chatgpt", "expires": None},
        )(),
    ):
        model = app.set_model(model="openai-codex")
        assert model == ModelResolver.resolve_alias("openai-codex")
        assert mock_agent.replace_primary_provider.called
        provider = mock_agent.replace_primary_provider.call_args[0][0]
        assert provider.model == ModelResolver.resolve_alias("openai-codex")
        assert provider.base_url == "https://chatgpt.com/backend-api"


def test_xcode_app_rejects_unsupported_model_effort() -> None:
    from unittest.mock import MagicMock

    from xcode.ai.providers.registry import ModelProfileConfig
    from xcode.coding_agent.app import XcodeApp

    app = XcodeApp(
        agent=MagicMock(),
        _model_profiles={"main": ModelProfileConfig(transport="openai_codex")},
    )
    credential = SimpleNamespace(
        access="tok-chatgpt",
        account_id="acc-chatgpt",
        expires=None,
    )

    with (
        patch(
            "xcode.harness.auth.manager.AuthManager.get_valid_credential",
            return_value=credential,
        ),
        pytest.raises(ValueError, match="does not support reasoning effort 'minimal'"),
    ):
        app.set_model(
            model="gpt-5.6-luna",
            transport="openai_codex",
            reasoning_effort="minimal",
        )


def test_xcode_app_model_info_uses_on_off_thinking_labels() -> None:
    from xcode.coding_agent.app import XcodeApp

    provider = SimpleNamespace(
        model="gpt-5.6-luna",
        base_url="https://chatgpt.com/backend-api",
        transport="openai_codex",
        thinking=True,
        reasoning_effort="low",
    )
    app = XcodeApp(agent=SimpleNamespace(provider=provider))

    assert app.get_model_info()["thinking"] == "on"
    provider.thinking = False
    assert app.get_model_info()["thinking"] == "off"


def test_get_available_model_entries_filters_unconfigured() -> None:
    from xcode.ai.models import get_codex_models
    from xcode.cli.repl_settings import get_available_model_entries

    app = DummyApp()
    # 模拟没有任何环境变量，仅有 openai-codex 认证
    with (
        patch("os.environ.get", return_value=None),
        patch(
            "xcode.harness.auth.manager.AuthManager.get_valid_credential",
            return_value=type(
                "Cred",
                (),
                {"access": "tok-123", "account_id": "acc-123", "expires": None},
            )(),
        ),
    ):
        entries = get_available_model_entries(app)
        codex_entries = [entry for entry in entries if entry.provider == "openai-codex"]
        assert [entry.model for entry in codex_entries] == [
            model.id for model in get_codex_models()
        ]
        assert all(entry.source_label == "[codex]" for entry in codex_entries)
        assert all(entry.provider not in {"chatglm", "mimo"} for entry in entries)


def test_current_model_keeps_provider_label() -> None:
    from xcode.ai.models import get_codex_models
    from xcode.cli.repl_settings import AvailableModelEntry, _format_model_entry

    current_model = get_codex_models()[0].id
    rendered = _format_model_entry(
        AvailableModelEntry(
            model=current_model,
            transport="openai_codex",
            provider="openai-codex",
            source_label="[codex]",
        )
    )

    assert "[codex]" in rendered
    assert "[current]" not in rendered


def test_get_available_model_entries_excludes_subagent_only_profile() -> None:
    from xcode.cli.repl_settings import get_available_model_entries

    app = DummyApp()
    app.current_model = "main-model"
    app.current_transport = "custom"
    app._model_profiles = {
        "subagent": SimpleNamespace(
            transport="deepseek_chat",
            chat_model="deepseek-v4-flash",
            api_key="subagent-secret",
        )
    }
    with (
        patch("os.environ.get", return_value=None),
        patch(
            "xcode.harness.auth.manager.AuthManager.get_valid_credential",
            return_value=None,
        ),
    ):
        entries = get_available_model_entries(app)

    assert all(entry.model != "deepseek-v4-flash" for entry in entries)


def test_get_available_model_entries_deduplicates_current_transport() -> None:
    from xcode.ai.models import get_codex_models
    from xcode.cli.repl_settings import get_available_model_entries

    app = DummyApp()
    app.current_model = get_codex_models()[0].id
    app.current_transport = "openai_responses"
    with (
        patch("os.environ.get", return_value=None),
        patch(
            "xcode.harness.auth.manager.AuthManager.get_valid_credential",
            return_value=type(
                "Cred",
                (),
                {"access": "tok-123", "account_id": "acc-123", "expires": None},
            )(),
        ),
    ):
        entries = get_available_model_entries(app)

    current_entries = [entry for entry in entries if entry.model == app.current_model]
    assert len(current_entries) == 1
    assert current_entries[0].transport == "openai_responses"


def test_handle_model_command_escape_cancel_has_no_choice() -> None:
    app = DummyApp()
    with (
        patch("sys.stdin.isatty", return_value=True),
        patch("questionary.select") as mock_select,
    ):
        mock_select.return_value.ask.return_value = None
        handle_model_command("/model", app)

    choices = mock_select.call_args.kwargs["choices"]
    assert all(choice.value != ("__cancel__", None) for choice in choices)
    assert len(app.calls) == 0


def test_read_repl_text_double_ctrl_c_exits() -> None:
    from unittest.mock import MagicMock

    from xcode.cli.commands import ReplState
    from xcode.cli.repl import _read_repl_text

    state = ReplState()
    mock_session = MagicMock()
    mock_session.prompt.side_effect = KeyboardInterrupt()
    mock_store = MagicMock()

    # 第一次按 Ctrl+C：记录 exit_pending，不退出
    text, should_exit = _read_repl_text(state, mock_session, mock_store)
    assert text is None
    assert should_exit is False
    assert state.exit_pending > 0

    # 3 秒内第二次按 Ctrl+C：触发退出
    text, should_exit = _read_repl_text(state, mock_session, mock_store)
    assert text is None
    assert should_exit is True


def test_read_repl_text_double_ctrl_d_exits() -> None:
    from unittest.mock import MagicMock

    from xcode.cli.commands import ReplState
    from xcode.cli.repl import _read_repl_text

    state = ReplState()
    mock_session = MagicMock()
    mock_session.prompt.side_effect = EOFError()
    mock_store = MagicMock()

    # 第一次按 Ctrl+D：记录 exit_pending，不退出
    text, should_exit = _read_repl_text(state, mock_session, mock_store)
    assert text is None
    assert should_exit is False
    assert state.exit_pending > 0

    # 3 秒内第二次按 Ctrl+D：触发退出
    text, should_exit = _read_repl_text(state, mock_session, mock_store)
    assert text is None
    assert should_exit is True


def test_terminal_isolated_guard() -> None:
    from xcode.cli.ptk_patch import terminal_isolated

    with (
        patch("xcode.cli.ptk_patch.get_console_mode", return_value=0x1234),
        patch("xcode.cli.ptk_patch.set_console_mode") as mock_set,
        patch("xcode.cli.ptk_patch.flush_console_input_buffer") as mock_flush,
    ):
        with terminal_isolated():
            pass

        assert mock_flush.called
        mock_set.assert_called_once_with(0x1234)


def test_safe_select_catches_interrupt() -> None:
    from xcode.cli.ptk_patch import safe_select

    with patch("questionary.select") as mock_select:
        mock_select.return_value.ask.side_effect = KeyboardInterrupt()
        res = safe_select("test", ["a", "b"], default="fallback")
        assert res == "fallback"


def test_safe_select_binds_escape_to_cancel() -> None:
    from xcode.cli.ptk_patch import safe_select

    with patch("questionary.select") as mock_select:
        question = mock_select.return_value
        question.ask.return_value = None

        assert safe_select("test", ["a", "b"]) is None

    question.application.key_bindings.add.assert_called_once_with("escape", eager=True)


def test_safe_text_catches_eof() -> None:
    from xcode.cli.ptk_patch import safe_text

    with patch("questionary.text") as mock_text:
        mock_text.return_value.ask.side_effect = EOFError()
        res = safe_text("test")
        assert res is None


def test_prompt_session_adapter_flushes_buffer() -> None:
    from unittest.mock import MagicMock

    from xcode.cli.repl_rendering import PromptSessionAdapter

    mock_raw_session = MagicMock()
    mock_raw_session.prompt.return_value = "hello"

    adapter = PromptSessionAdapter(mock_raw_session)
    with patch("xcode.cli.ptk_patch.flush_console_input_buffer") as mock_flush:
        val = adapter.prompt("> ")
        assert val == "hello"
        assert mock_flush.called
