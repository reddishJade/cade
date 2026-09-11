"""测试 REPL /model 命令切换逻辑。"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from xcode.cli.repl_settings import handle_model_command


class DummyApp:
    def __init__(self) -> None:
        self.current_model = "deepseek-v4-flash"
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
    assert "Model    : deepseek-v4-flash" in captured
    assert "用法: /model" in captured


def test_handle_model_command_codex_alias() -> None:
    app = DummyApp()
    handle_model_command("/model codex", app)
    assert len(app.calls) == 1
    call = app.calls[0]
    assert call["model"] == "gpt-5.3-codex"
    assert call["transport"] == "openai_codex"
    assert call["profile"] == "main"


def test_handle_model_command_openai_codex_alias() -> None:
    app = DummyApp()
    handle_model_command("/model openai-codex", app)
    assert len(app.calls) == 1
    call = app.calls[0]
    assert call["model"] == "gpt-5.3-codex"
    assert call["transport"] == "openai_codex"


def test_handle_model_command_provider_prefix() -> None:
    app = DummyApp()
    handle_model_command("/model deepseek/deepseek-v4-pro", app)
    assert len(app.calls) == 1
    call = app.calls[0]
    assert call["model"] == "deepseek-v4-pro"
    assert call["transport"] == "deepseek_chat"


def test_handle_model_command_thinking_level() -> None:
    app = DummyApp()
    handle_model_command("/model gpt-5.5:high", app)
    assert len(app.calls) == 1
    call = app.calls[0]
    assert call["model"] == "gpt-5.5"
    assert call["thinking"] is True
    assert call["reasoning_effort"] == "high"


def test_handle_model_command_thinking_flag() -> None:
    app = DummyApp()
    handle_model_command("/model deepseek-v4-pro --thinking off", app)
    assert len(app.calls) == 1
    call = app.calls[0]
    assert call["model"] == "deepseek-v4-pro"
    assert call["thinking"] is False
    assert call["reasoning_effort"] is None


def test_handle_model_command_interactive_select() -> None:
    app = DummyApp()
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
        mock_select.return_value.ask.return_value = ("gpt-5.5", "openai_codex")
        handle_model_command("/model", app)

    assert len(app.calls) == 1
    call = app.calls[0]
    assert call["model"] == "gpt-5.5"
    assert call["transport"] == "openai_codex"


def test_xcode_app_set_model_smart_inference() -> None:
    from unittest.mock import MagicMock

    from xcode.ai.providers.registry import ModelProfileConfig
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
        assert model == "gpt-5.3-codex"
        assert mock_agent.replace_primary_provider.called
        provider = mock_agent.replace_primary_provider.call_args[0][0]
        assert provider.model == "gpt-5.3-codex"
        assert provider.base_url == "https://chatgpt.com/backend-api"


def test_get_available_model_entries_filters_unconfigured() -> None:
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
        model_names = [e.model for e in entries]
        # 应该包含 openai-codex 的模型和当前模型
        assert "gpt-6-astra" in model_names
        assert "gpt-5.3-codex" in model_names
        assert "gpt-5.5" in model_names
        assert "gpt-5.4" in model_names
        assert "deepseek-v4-flash" in model_names  # 当前运行模型
        # 验证简化的标签格式为 [codex]
        codex_entry = next(e for e in entries if e.model == "gpt-5.3-codex")
        assert codex_entry.source_label == "[codex]"
        # 不应包含未配置的 glm 或 mimo 模型
        assert "glm-5.1" not in model_names
        assert "mimo-v2.5-pro" not in model_names


def test_handle_model_command_cancel() -> None:
    app = DummyApp()
    with (
        patch("sys.stdin.isatty", return_value=True),
        patch("questionary.select") as mock_select,
    ):
        mock_select.return_value.ask.return_value = ("__cancel__", None)
        handle_model_command("/model", app)

    # 取消时不应调用 set_model
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
