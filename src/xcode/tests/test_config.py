"""运行时配置 schema 与合并逻辑的单元测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from xcode.harness.config import XcodeRuntimeConfig, _config_from_dict
from xcode.harness.execution_env import NetworkAccess, SandboxMode


class TestProviderContextWindow:
    def test_defaults_to_none(self) -> None:
        cfg = XcodeRuntimeConfig()
        assert cfg.provider.model_profiles["main"].context_window is None

    def test_parse_override(self) -> None:
        cfg = XcodeRuntimeConfig.model_validate(
            {"provider": {"model_profiles": {"main": {"context_window": 262_144}}}}
        )
        assert cfg.provider.model_profiles["main"].context_window == 262_144

    def test_null_means_registry_default(self) -> None:
        cfg = XcodeRuntimeConfig.model_validate(
            {"provider": {"model_profiles": {"main": {"context_window": None}}}}
        )
        assert cfg.provider.model_profiles["main"].context_window is None

    def test_non_positive_rejected(self) -> None:
        with pytest.raises(ValidationError):
            XcodeRuntimeConfig.model_validate(
                {"provider": {"model_profiles": {"main": {"context_window": 0}}}}
            )

    def test_non_int_rejected(self) -> None:
        with pytest.raises(ValidationError):
            XcodeRuntimeConfig.model_validate(
                {"provider": {"model_profiles": {"main": {"context_window": "262144"}}}}
            )


class TestExecutionModesDefaultMode:
    def test_defaults_to_act(self) -> None:
        cfg = XcodeRuntimeConfig()
        assert cfg.execution_modes.default_mode == "act"

    def test_parse_build(self) -> None:
        cfg = XcodeRuntimeConfig.model_validate(
            {"execution_modes": {"default_mode": "build"}}
        )
        assert cfg.execution_modes.default_mode == "build"

    def test_parse_plan(self) -> None:
        cfg = XcodeRuntimeConfig.model_validate(
            {"execution_modes": {"default_mode": "plan"}}
        )
        assert cfg.execution_modes.default_mode == "plan"

    def test_invalid_value_rejected(self) -> None:
        with pytest.raises(ValidationError):
            XcodeRuntimeConfig.model_validate(
                {"execution_modes": {"default_mode": "auto"}}
            )

    def test_invalid_value_error_names_field(self) -> None:
        with pytest.raises(ValueError, match="execution_modes.default_mode"):
            _config_from_dict({"execution_modes": {"default_mode": "auto"}})

    def test_unknown_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            XcodeRuntimeConfig.model_validate(
                {"execution_modes": {"initial_mode": "build"}}
            )


class TestApprovalConfiguration:
    def test_defaults_to_on_request(self) -> None:
        cfg = XcodeRuntimeConfig()

        assert cfg.security.approval_policy == "on-request"

    def test_removed_always_value_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            XcodeRuntimeConfig.model_validate(
                {"security": {"approval_policy": "always"}}
            )

    def test_global_reviewer_switch_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            XcodeRuntimeConfig.model_validate(
                {"security": {"approvals_reviewer": "user"}}
            )


class TestSandboxConfiguration:
    def test_defaults_to_workspace_write_without_network(self) -> None:
        cfg = XcodeRuntimeConfig()

        assert cfg.security.sandbox.mode is SandboxMode.WORKSPACE_WRITE
        assert cfg.security.sandbox.network_access is NetworkAccess.DENY

    def test_parses_explicit_full_access(self) -> None:
        cfg = XcodeRuntimeConfig.model_validate(
            {
                "security": {
                    "sandbox": {
                        "mode": "danger-full-access",
                        "network_access": "allow",
                    }
                }
            }
        )

        assert cfg.security.sandbox.mode is SandboxMode.DANGER_FULL_ACCESS
        assert cfg.security.sandbox.network_access is NetworkAccess.ALLOW

    def test_rejects_unknown_sandbox_mode(self) -> None:
        with pytest.raises(ValidationError):
            XcodeRuntimeConfig.model_validate(
                {"security": {"sandbox": {"mode": "directory-check"}}}
            )


class TestDiscoverRuntimeConfigOAuthFallback:
    def test_oauth_fallback_when_no_api_key_configured(self, tmp_path: Path) -> None:
        from unittest.mock import patch

        from xcode.harness.config import discover_runtime_config

        project_dir = tmp_path / "empty_project"
        project_dir.mkdir()
        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()

        mock_cred = type(
            "MockCred",
            (),
            {
                "access": "test-oauth-token",
                "account_id": "acc-12345",
                "expires": None,
                "refresh": None,
            },
        )()

        with (
            patch("pathlib.Path.home", return_value=fake_home),
            patch.dict("os.environ", {"USERPROFILE": str(fake_home)}, clear=False),
            patch(
                "xcode.harness.auth.manager.AuthManager.get_valid_credential",
                return_value=mock_cred,
            ),
        ):
            cfg = discover_runtime_config(project_dir)
            main_profile = cfg.provider.model_profiles["main"]
            from xcode.ai.resolver import ModelResolver

            assert main_profile.transport == "openai_codex"
            assert main_profile.chat_model == ModelResolver.resolve_alias("codex")
            assert main_profile.api_key == "test-oauth-token"
            assert main_profile.account_id == "acc-12345"
            assert main_profile.base_url == "https://chatgpt.com/backend-api"

    def test_preserves_project_config_api_key(self, tmp_path: Path) -> None:
        import json
        from unittest.mock import patch

        from xcode.harness.config import discover_runtime_config

        project_dir = tmp_path / "project_with_config"
        project_dir.mkdir()
        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()

        config_file = project_dir / "xcode.config.json"
        config_file.write_text(
            json.dumps(
                {
                    "provider": {
                        "model_profiles": {
                            "main": {
                                "transport": "openai_chat",
                                "chat_model": "deepseek-chat",
                                "api_key": "sk-proj-key",
                                "base_url": "https://api.deepseek.com",
                            }
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        mock_cred = type(
            "MockCred",
            (),
            {
                "access": "test-oauth-token",
                "account_id": "acc-12345",
                "expires": None,
                "refresh": None,
            },
        )()

        with (
            patch("pathlib.Path.home", return_value=fake_home),
            patch.dict("os.environ", {"USERPROFILE": str(fake_home)}, clear=False),
            patch(
                "xcode.harness.auth.manager.AuthManager.get_valid_credential",
                return_value=mock_cred,
            ),
        ):
            cfg = discover_runtime_config(project_dir)
            main_profile = cfg.provider.model_profiles["main"]
            assert main_profile.transport == "openai_chat"
            assert main_profile.chat_model == "deepseek-chat"
            assert main_profile.api_key == "sk-proj-key"

    def test_oauth_wins_over_env_api_key(self, tmp_path: Path) -> None:
        from unittest.mock import patch

        from xcode.harness.config import discover_runtime_config

        project_dir = tmp_path / "env_project"
        project_dir.mkdir()
        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()

        mock_cred = type(
            "MockCred",
            (),
            {
                "access": "test-oauth-token",
                "account_id": "acc-12345",
                "expires": None,
                "refresh": None,
            },
        )()

        with (
            patch("pathlib.Path.home", return_value=fake_home),
            patch.dict(
                "os.environ",
                {
                    "USERPROFILE": str(fake_home),
                    "OPENAI_API_KEY": "sk-env-openai",
                    "DEEPSEEK_API_KEY": "sk-env-deepseek",
                },
                clear=False,
            ),
            patch(
                "xcode.harness.auth.manager.AuthManager.get_valid_credential",
                return_value=mock_cred,
            ),
        ):
            cfg = discover_runtime_config(project_dir)
            main_profile = cfg.provider.model_profiles["main"]
            assert main_profile.transport == "openai_codex"
            assert main_profile.api_key == "test-oauth-token"
            assert main_profile.base_url == "https://chatgpt.com/backend-api"

    def test_oauth_wins_over_openai_profile_api_key(self, tmp_path: Path) -> None:
        import json
        from unittest.mock import patch

        from xcode.harness.config import discover_runtime_config

        project_dir = tmp_path / "openai_project"
        project_dir.mkdir()
        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()

        (project_dir / "xcode.config.json").write_text(
            json.dumps(
                {
                    "provider": {
                        "model_profiles": {
                            "main": {
                                "transport": "openai_chat",
                                "chat_model": "gpt-5.6-sol",
                                "api_key": "sk-openai",
                                "base_url": "https://api.openai.com/v1",
                            }
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        mock_cred = type(
            "MockCred",
            (),
            {
                "access": "test-oauth-token",
                "account_id": "acc-12345",
                "expires": None,
                "refresh": None,
            },
        )()

        with (
            patch("pathlib.Path.home", return_value=fake_home),
            patch.dict("os.environ", {"USERPROFILE": str(fake_home)}, clear=False),
            patch(
                "xcode.harness.auth.manager.AuthManager.get_valid_credential",
                return_value=mock_cred,
            ),
        ):
            cfg = discover_runtime_config(project_dir)
            main_profile = cfg.provider.model_profiles["main"]
            assert main_profile.transport == "openai_codex"
            assert main_profile.api_key == "test-oauth-token"
            assert main_profile.chat_model == "gpt-5.6-sol"
            assert main_profile.base_url == "https://chatgpt.com/backend-api"
