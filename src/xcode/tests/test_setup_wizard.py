"""配置向导与配置探测单元测试。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

from xcode.cli.setup_wizard import (
    _check_config_has_api_key,
    has_auth_credential,
    has_api_key,
    has_valid_config,
    prompt_login_method,
    run_setup_wizard,
)


class TestCheckConfigHasApiKey:
    def test_returns_true_when_api_key_present(self, tmp_path: Path) -> None:
        cfg = tmp_path / "xcode.config.json"
        cfg.write_text(
            json.dumps(
                {
                    "provider": {
                        "model_profiles": {
                            "main": {"api_key": "sk-123456"},
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        assert _check_config_has_api_key(cfg) is True

    def test_returns_false_for_transport_without_api_key(self, tmp_path: Path) -> None:
        cfg = tmp_path / "xcode.config.json"
        cfg.write_text(
            json.dumps(
                {
                    "provider": {
                        "model_profiles": {
                            "main": {"transport": "openai_codex"},
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        assert _check_config_has_api_key(cfg) is False

    def test_returns_false_when_empty_or_missing(self, tmp_path: Path) -> None:
        cfg = tmp_path / "xcode.config.json"
        assert _check_config_has_api_key(cfg) is False
        cfg.write_text("{}", encoding="utf-8")
        assert _check_config_has_api_key(cfg) is False


class TestHasValidConfig:
    def test_returns_true_with_config_api_key(self, tmp_path: Path) -> None:
        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / "xcode.config.json").write_text(
            json.dumps(
                {
                    "provider": {
                        "model_profiles": {"main": {"api_key": "sk-project"}},
                    }
                }
            ),
            encoding="utf-8",
        )
        with (
            patch("pathlib.Path.home", return_value=fake_home),
            patch(
                "xcode.harness.auth.manager.AuthManager.get_valid_credential",
                return_value=None,
            ),
        ):
            assert has_valid_config(project_dir) is True

    def test_returns_true_with_valid_oauth_credential(self, tmp_path: Path) -> None:
        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()
        mock_cred = type("Cred", (), {"access": "token"})()
        with (
            patch("pathlib.Path.home", return_value=fake_home),
            patch.dict("os.environ", {"USERPROFILE": str(fake_home)}, clear=False),
            patch(
                "xcode.harness.auth.manager.AuthManager.get_valid_credential",
                return_value=mock_cred,
            ),
        ):
            assert has_valid_config(tmp_path) is True

    def test_returns_false_when_no_config_and_no_oauth(self, tmp_path: Path) -> None:
        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()
        with (
            patch("pathlib.Path.home", return_value=fake_home),
            patch.dict("os.environ", {"USERPROFILE": str(fake_home)}, clear=False),
            patch(
                "xcode.harness.auth.manager.AuthManager.get_valid_credential",
                return_value=None,
            ),
        ):
            # 移除已知可能存在的 API key 变量
            for k in (
                "MAIN_API_KEY",
                "OPENAI_API_KEY",
                "ANTHROPIC_API_KEY",
                "DEEPSEEK_API_KEY",
                "MIMO_API_KEY",
                "CHATGLM_API_KEY",
                "ZHIPUAI_API_KEY",
                "BIGMODEL_API_KEY",
                "API_KEY",
            ):
                os.environ.pop(k, None)
            assert has_valid_config(tmp_path) is False

    def test_returns_true_with_env_api_key(self, tmp_path: Path) -> None:
        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()
        with (
            patch("pathlib.Path.home", return_value=fake_home),
            patch.dict(
                "os.environ",
                {"DEEPSEEK_API_KEY": "sk-deepseek", "USERPROFILE": str(fake_home)},
                clear=False,
            ),
            patch(
                "xcode.harness.auth.manager.AuthManager.get_valid_credential",
                return_value=None,
            ),
        ):
            assert has_valid_config(tmp_path) is True

    def test_returns_false_with_transport_only_config(self, tmp_path: Path) -> None:
        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / "xcode.config.json").write_text(
            json.dumps(
                {
                    "provider": {
                        "model_profiles": {"main": {"transport": "openai_codex"}},
                    }
                }
            ),
            encoding="utf-8",
        )
        with (
            patch("pathlib.Path.home", return_value=fake_home),
            patch(
                "xcode.harness.auth.manager.AuthManager.get_valid_credential",
                return_value=None,
            ),
        ):
            for key in (
                "MAIN_API_KEY",
                "OPENAI_API_KEY",
                "ANTHROPIC_API_KEY",
                "DEEPSEEK_API_KEY",
                "MIMO_API_KEY",
                "CHATGLM_API_KEY",
                "ZHIPUAI_API_KEY",
                "BIGMODEL_API_KEY",
                "API_KEY",
            ):
                os.environ.pop(key, None)
            assert has_valid_config(project_dir) is False


class TestCredentialProbes:
    def test_has_auth_credential_reads_oauth_store(self, tmp_path: Path) -> None:
        mock_cred = type("Cred", (), {"access": "token"})()
        with patch(
            "xcode.harness.auth.manager.AuthManager.get_valid_credential",
            return_value=mock_cred,
        ):
            assert has_auth_credential() is True

    def test_has_api_key_reads_env(self, tmp_path: Path) -> None:
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-openai"}, clear=False):
            assert has_api_key(tmp_path) is True


class TestPromptLoginMethod:
    def test_returns_auth_for_default_choice(self) -> None:
        with patch("questionary.select") as mock_select:
            mock_select.return_value.ask.return_value = (
                "Sign in with ChatGPT (OAuth, recommended)"
            )
            assert prompt_login_method() == "auth"

    def test_returns_api_for_api_choice(self) -> None:
        with patch("questionary.select") as mock_select:
            mock_select.return_value.ask.return_value = "Configure an API key"
            assert prompt_login_method() == "api"

    def test_returns_none_on_cancel(self) -> None:
        with patch("questionary.select") as mock_select:
            mock_select.return_value.ask.return_value = None
            assert prompt_login_method() is None


class TestRunSetupWizardSaveLocations:
    def test_save_global_default(self, tmp_path: Path) -> None:
        fake_home = tmp_path / "fake_home"
        project_dir = tmp_path / "project"
        project_dir.mkdir(parents=True, exist_ok=True)

        with (
            patch("pathlib.Path.home", return_value=fake_home),
            patch("questionary.select") as mock_select,
            patch("questionary.text") as mock_text,
        ):
            mock_select.return_value.ask.side_effect = [
                "DeepSeek",
                "deepseek-v4-flash",
                "enabled",
                "high",
                "Global default (~/.xcode/settings.json, recommended)",
            ]
            mock_text.return_value.ask.side_effect = [
                "sk-my-deepseek-key",
                "https://api.deepseek.com",
            ]

            status, path = run_setup_wizard(project_dir)
            assert status == "saved"
            assert path is None

            global_file = fake_home / ".xcode" / "settings.json"
            assert global_file.exists()
            content = json.loads(global_file.read_text(encoding="utf-8"))
            main = content["provider"]["model_profiles"]["main"]
            assert main["api_key"] == "sk-my-deepseek-key"
            assert main["chat_model"] == "deepseek-v4-flash"
