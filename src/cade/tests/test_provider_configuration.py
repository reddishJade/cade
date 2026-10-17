"""配置与凭据公开契约的离线回归；真实宿主验收见随附说明。"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from cade.ai.auth.types import AuthCredential
from cade.coding_agent.assembly.providers import resolve_model_profiles
from cade.coding_agent.modes.exec_mode import prepare_exec_config
from cade.coding_agent.modes.tui import setup_wizard
from cade.harness.auth import store as auth_store
from cade.harness.auth.manager import AuthManager
from cade.harness.auth.store import AuthStore
from cade.harness.config import CadeRuntimeConfig, discover_runtime_config
from cade.main import parse_args


def write_config(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def isolated_root(monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """使用仓库内的独立目录和合成凭据，不读取用户认证。"""
    repository = Path(__file__).resolve().parents[3]
    results = repository / "e2e-results"
    results.mkdir(exist_ok=True)
    if not results.resolve().is_relative_to(repository):
        raise RuntimeError("regression directory must remain inside the repository")
    with tempfile.TemporaryDirectory(prefix="config-regression-", dir=results) as run:
        root = Path(run)
        home = root / "home"
        home.mkdir()
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
        monkeypatch.setattr(auth_store, "DEFAULT_AUTH_FILE", home / ".cade/auth.json")
        for name in (
            "CADE_APPROVAL_POLICY",
            "OPENAI_API_KEY",
            "DEEPSEEK_API_KEY",
            "CHATGLM_API_KEY",
            "ZHIPUAI_API_KEY",
            "BIGMODEL_API_KEY",
            "MIMO_API_KEY",
            "API_KEY",
        ):
            monkeypatch.delenv(name, raising=False)
        yield root


def seed_credentials() -> None:
    """凭据均为测试占位值，禁止进行网络调用。"""
    store = AuthStore()
    store.save(
        AuthCredential(
            provider="openai-codex",
            type="oauth",
            access="fixture-oauth",
            account_id="fixture-account",
        )
    )
    AuthManager(store=store).save_api_key("deepseek", "fixture-deepseek")


def test_explicit_config_replaces_project_but_keeps_other_layers(
    isolated_root: Path,
) -> None:
    seed_credentials()
    workspace = isolated_root / "workspace"
    write_config(
        Path.home() / ".cade/settings.json",
        {
            "default_reasoning_effort": "low",
            "provider": {
                "model_profiles": {
                    "subagent": {
                        "provider": "deepseek",
                        "model": "deepseek-flash",
                        "thinking": False,
                    }
                }
            },
        },
    )
    write_config(workspace / "cade.config.json", {"default_model": "unused-project"})
    explicit = isolated_root / "run.json"
    write_config(
        explicit,
        {
            "default_model": "gpt-5.6-luna",
            "provider": {"model_profiles": {"subagent": {"context_window": 65536}}},
        },
    )
    write_config(
        workspace / ".cade/settings.json",
        {"default_reasoning_effort": "high"},
    )
    config = discover_runtime_config(workspace, explicit)
    profiles = resolve_model_profiles(config, ())
    assert profiles["main"].chat_model == "gpt-5.6-luna"
    assert profiles["main"].reasoning_effort == "high"
    child = profiles["subagent"]
    assert (child.provider, child.chat_model) == ("deepseek", "deepseek-flash")
    assert child.context_window == 65536
    assert child.thinking is False
    assert child.reasoning_effort == "high"


def test_omitted_roles_inherit_and_explicit_null_false_override(
    isolated_root: Path,
) -> None:
    seed_credentials()
    config = CadeRuntimeConfig.model_validate(
        {
            "provider": {
                "options": {"context_window": 65536, "thinking": True},
                "model_profiles": {
                    "reviewer": {
                        "context_window": None,
                        "thinking": False,
                        "reasoning_effort": None,
                    }
                },
            }
        }
    )
    profiles = resolve_model_profiles(config, ())
    for role in ("subagent", "fallback"):
        assert profiles[role] == profiles["main"]
    reviewer = profiles["reviewer"]
    assert reviewer.provider == "openai-codex"
    assert reviewer.context_window is None
    assert reviewer.thinking is False
    assert reviewer.reasoning_effort is None


def test_exec_model_override_keeps_explicit_role_selection(
    isolated_root: Path,
) -> None:
    seed_credentials()
    config = CadeRuntimeConfig.model_validate(
        {
            "default_provider": "deepseek",
            "default_model": "deepseek-flash",
            "provider": {
                "model_profiles": {
                    "reviewer": {
                        "provider": "deepseek",
                        "model": "deepseek-flash",
                        "reasoning_effort": "high",
                    }
                }
            },
        }
    )
    before = config.model_dump()
    args = parse_args(
        [
            "exec",
            "--model",
            "openai-codex/gpt-5.6-luna",
            "--reasoning-effort",
            "high",
            "hi",
        ]
    )
    profiles = resolve_model_profiles(prepare_exec_config(config, args), ())
    assert profiles["main"].provider == "openai-codex"
    assert profiles["subagent"] == profiles["main"]
    assert profiles["fallback"] == profiles["main"]
    assert profiles["reviewer"].provider == "deepseek"
    assert profiles["reviewer"].api_key == "fixture-deepseek"
    assert config.model_dump() == before


def test_api_login_preserves_defaults_roles_and_oauth(
    isolated_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_credentials()
    settings = Path.home() / ".cade/settings.json"
    original = {
        "default_provider": "openai-codex",
        "default_model": "gpt-5.6-luna",
        "default_reasoning_effort": "high",
        "provider": {"model_profiles": {"reviewer": {"thinking": False}}},
    }
    write_config(settings, original)
    monkeypatch.setattr(setup_wizard, "safe_select", lambda *args, **kwargs: "DeepSeek")
    monkeypatch.setattr(
        setup_wizard,
        "safe_text",
        lambda message, **kwargs: (
            "fixture-new-deepseek"
            if message == "API Key:"
            else "https://api.deepseek.com"
        ),
    )
    setup_wizard.run_setup_wizard(isolated_root, from_connect=True)
    saved = json.loads(settings.read_text(encoding="utf-8"))
    for field in ("default_provider", "default_model", "default_reasoning_effort"):
        assert saved[field] == original[field]
    assert saved["provider"]["model_profiles"] == original["provider"]["model_profiles"]
    credentials = AuthStore().load_all()
    assert credentials["openai-codex"].access == "fixture-oauth"
    assert credentials["deepseek"].access == "fixture-new-deepseek"
    assert "fixture-new-deepseek" not in settings.read_text(encoding="utf-8")


def test_api_and_oauth_are_selected_independently(isolated_root: Path) -> None:
    seed_credentials()
    AuthManager().save_api_key("openai", "fixture-openai-api")
    config = CadeRuntimeConfig.model_validate(
        {
            "provider": {
                "model_profiles": {
                    "reviewer": {"provider": "openai", "model": "gpt-5.6-luna"}
                }
            }
        }
    )
    profiles = resolve_model_profiles(config, ())
    assert profiles["main"].transport == "openai_codex"
    assert profiles["main"].api_key == "fixture-oauth"
    assert profiles["reviewer"].transport == "openai_responses"
    assert profiles["reviewer"].api_key == "fixture-openai-api"
    assert profiles["reviewer"].account_id is None


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="自定义 provider 按 transport 回退到了其他 provider 的环境 key",
)
def test_custom_provider_does_not_borrow_openai_key(
    isolated_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-official-only")
    config = CadeRuntimeConfig.model_validate(
        {
            "default_provider": "acme",
            "default_model": "acme-model",
            "provider": {
                "connections": {
                    "acme": {
                        "transport": "openai_responses",
                        "base_url": "https://acme.invalid/v1",
                    }
                }
            },
        }
    )
    try:
        resolve_model_profiles(config, ())
    except RuntimeError:
        return
    raise AssertionError("acme accepted an API key belonging to openai")


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="登录向导的默认输入将现有环境 API key 截成前 16 个字符",
)
def test_accepting_existing_api_key_keeps_complete_value(
    isolated_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = "fixture-complete-deepseek-key"
    monkeypatch.setenv("DEEPSEEK_API_KEY", key)
    monkeypatch.setattr(setup_wizard, "safe_select", lambda *args, **kwargs: "DeepSeek")
    monkeypatch.setattr(
        setup_wizard, "safe_text", lambda message, **kwargs: kwargs["default"]
    )
    setup_wizard.run_setup_wizard(isolated_root, from_connect=True)
    credential = AuthStore().get("deepseek")
    assert credential is not None
    assert credential.access == key
