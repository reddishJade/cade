"""Cade 认证与凭据存储单元测试。"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

from cade.ai.auth import AuthCredential
from cade.cli.auth_cmd import (
    handle_login_command,
    handle_logout_command,
    handle_status_command,
)
from cade.cli.setup_wizard import prompt_auth_method
from cade.harness.auth.manager import AuthManager
from cade.harness.auth.store import AuthStore
from cade.main import parse_args


def test_auth_credential_serialization() -> None:
    now = int(time.time())
    cred = AuthCredential(
        provider="openai-codex",
        access="test-access-token",
        refresh="test-refresh-token",
        expires=now + 3600,
        account_id="chatgpt-acc-123",
        extra={"custom_key": "val"},
    )
    d = cred.to_dict()
    assert d["access"] == "test-access-token"
    assert d["refresh"] == "test-refresh-token"
    assert d["account_id"] == "chatgpt-acc-123"
    assert d["type"] == "oauth"

    loaded = AuthCredential.from_dict(d, provider="openai-codex")
    assert loaded.provider == "openai-codex"
    assert loaded.access == "test-access-token"
    assert loaded.refresh == "test-refresh-token"
    assert loaded.account_id == "chatgpt-acc-123"
    assert loaded.extra.get("custom_key") == "val"
    assert not loaded.is_expired()


def test_auth_store_lifecycle(tmp_path: Path) -> None:
    store_file = tmp_path / ".cade" / "auth.json"
    store = AuthStore(path=store_file)

    # 初始状态为空
    assert store.load_all() == {}
    assert store.get("openai-codex") is None

    # 保存凭据
    cred = AuthCredential(
        provider="openai-codex",
        access="token_abc",
        refresh="refresh_xyz",
        account_id="acc_999",
    )
    store.save(cred)

    assert store_file.exists()
    all_creds = store.load_all()
    assert "openai-codex" in all_creds
    assert all_creds["openai-codex"].access == "token_abc"
    assert all_creds["openai-codex"].account_id == "acc_999"

    # 获取
    retrieved = store.get("openai-codex")
    assert retrieved is not None
    assert retrieved.access == "token_abc"

    # 删除
    deleted = store.delete("openai-codex")
    assert deleted is True
    assert store.get("openai-codex") is None
    assert store.load_all() == {}

    # 删除不存在的
    assert store.delete("openai-codex") is False


def test_auth_manager_auto_refresh(tmp_path: Path) -> None:
    store_file = tmp_path / "auth.json"
    store = AuthStore(path=store_file)
    manager = AuthManager(store=store)

    # 即将过期的凭据（剩余 60 秒）
    expiring_cred = AuthCredential(
        provider="openai-codex",
        access="old_token",
        refresh="valid_refresh",
        expires=int(time.time()) + 60,
        account_id="acc_refresh_test",
    )
    store.save(expiring_cred)

    refreshed_cred = AuthCredential(
        provider="openai-codex",
        access="new_token",
        refresh="new_refresh",
        expires=int(time.time()) + 3600,
        account_id="acc_refresh_test",
    )

    with patch(
        "cade.ai.auth.openai_codex.refresh_openai_codex_token",
        return_value=refreshed_cred,
    ) as mock_refresh:
        got = manager.get_valid_credential("openai-codex")
        assert mock_refresh.call_count == 1
        assert got is not None
        assert got.access == "new_token"
        assert got.refresh == "new_refresh"

        # 检查是否已存入持久化存储
        saved = store.get("openai-codex")
        assert saved is not None
        assert saved.access == "new_token"


def test_auth_manager_delegates_login_to_ai_auth_provider(tmp_path: Path) -> None:
    store = AuthStore(path=tmp_path / "auth.json")
    credential = AuthCredential(provider="example", access="provider-token")

    class StubAuthProvider:
        id = "example"

        def login(
            self,
            *,
            method: str,
            notify_callback: Callable[..., None] | None = None,
        ) -> AuthCredential:
            assert method == "browser"
            assert notify_callback is None
            return credential

        def refresh(self, current: AuthCredential) -> AuthCredential:
            return current

    manager = AuthManager(store=store, providers={"example": StubAuthProvider()})

    assert manager.login("example") is credential
    assert store.get("example") == credential


def test_auth_manager_list_accounts(tmp_path: Path) -> None:
    store = AuthStore(path=tmp_path / "auth.json")
    manager = AuthManager(store=store)

    store.save(
        AuthCredential(
            provider="openai-codex",
            access="tok",
            account_id="test-acc",
            expires=int(time.time()) + 1800,
        )
    )

    accounts = manager.list_accounts()
    assert len(accounts) == 1
    assert accounts[0]["provider"] == "openai-codex"
    assert accounts[0]["account_id"] == "test-acc"
    assert accounts[0]["expired"] is False


def test_cli_auth_arguments() -> None:
    args_login = parse_args(["login"])
    assert args_login.command == "login"
    assert args_login.provider == "openai-codex"
    assert args_login.method == "browser"
    assert args_login._auth_method_explicit is False

    args_connect = parse_args(["connect"])
    assert args_connect.command == "connect"
    assert args_connect._auth_method_explicit is False

    args_api_key = parse_args(["login", "--method", "api_key"])
    assert args_api_key.method == "api_key"
    assert args_api_key._auth_method_explicit is True

    args_device = parse_args(
        ["login", "--provider", "openai-codex", "--method", "device_code"]
    )
    assert args_device.method == "device_code"

    args_logout = parse_args(["logout"])
    assert args_logout.command == "logout"

    args_auth_status = parse_args(["auth", "status"])
    assert args_auth_status.command == "auth"
    assert args_auth_status.auth_action == "status"

    args_auth_connect = parse_args(["auth", "connect"])
    assert args_auth_connect.auth_action == "connect"
    assert args_auth_connect._auth_method_explicit is False


def test_prompt_auth_method_choices() -> None:
    with patch("questionary.select") as mock_select:
        mock_select.return_value.unsafe_ask.return_value = "Sign in with an account"
        assert prompt_auth_method() == "account"
        kwargs = mock_select.call_args.kwargs
        assert kwargs["choices"] == [
            "Sign in with an account",
            "Sign in with an API key",
        ]

    with patch("questionary.select") as mock_select:
        mock_select.return_value.unsafe_ask.return_value = "Sign in with an API key"
        assert prompt_auth_method() == "api_key"


def test_cli_api_key_login_runs_provider_setup(tmp_path: Path) -> None:
    with (
        patch("cade.cli.auth_cmd.prompt_auth_method", return_value="api_key"),
        patch(
            "cade.cli.auth_cmd.run_setup_wizard",
            return_value=("saved", None),
        ) as wizard,
    ):
        assert handle_login_command(project_root=tmp_path) == 0

    wizard.assert_called_once_with(tmp_path, from_connect=True)


def test_cli_auth_handlers(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "cade.harness.auth.store.DEFAULT_AUTH_FILE", tmp_path / "auth.json"
    )

    # 模拟登录
    mock_cred = AuthCredential(
        provider="openai-codex",
        access="acc_123",
        account_id="chatgpt_user_1",
    )
    with patch.object(AuthManager, "login", return_value=mock_cred):
        assert handle_login_command("openai-codex", "browser") == 0

    # 模拟查看状态
    assert handle_status_command() == 0

    # 模拟登出
    with patch.object(AuthManager, "logout", return_value=True):
        assert handle_logout_command("openai-codex") == 0
