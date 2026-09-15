"""首次启动配置向导。"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

import questionary
from dotenv import dotenv_values

from cade.ai.models import get_codex_models
from cade.ai.resolver import ModelResolver

from .reasoning_effort import (
    reasoning_effort_levels_for_transport,
    supports_reasoning_effort,
)

JsonObject = dict[str, object]
CONFIG_FILENAME = "cade.config.json"
OPENAI_MODELS = [model.id for model in get_codex_models()]
API_KEY_ENV_NAMES = (
    "MAIN_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "DEEPSEEK_API_KEY",
    "MIMO_API_KEY",
    "CHATGLM_API_KEY",
    "ZHIPUAI_API_KEY",
    "BIGMODEL_API_KEY",
    "API_KEY",
)
LOGIN_CHOICE_AUTH = "Sign in with ChatGPT (OAuth, recommended)"
LOGIN_CHOICE_API = "Configure an API key"

PROVIDER_PRESETS: dict[str, Any] = {
    "openai": {
        "label": "OpenAI",
        "base_url": "",
        "models": OPENAI_MODELS,
        "default_model": ModelResolver.resolve_alias("codex"),
        "env_key": "OPENAI_API_KEY",
        "env_base_url": "OPENAI_BASE_URL",
    },
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "models": ["deepseek-v4-flash", "deepseek-v4-pro"],
        "default_model": "deepseek-v4-flash",
        "env_key": "DEEPSEEK_API_KEY",
        "env_base_url": "DEEPSEEK_BASE_URL",
    },
    "mimo": {
        "label": "Xiaomi MiMo",
        "base_url": "https://api.xiaomimimo.com/v1",
        "models": ["mimo-v2.5-pro", "mimo-v2.5"],
        "default_model": "mimo-v2.5-pro",
        "env_key": "MIMO_API_KEY",
        "env_base_url": "MIMO_BASE_URL",
    },
    "chatglm": {
        "label": "ChatGLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4/",
        "models": ["glm-4.7", "glm-4-flash", "glm-5", "glm-5.1"],
        "default_model": "glm-4.7",
        "env_key": "CHATGLM_API_KEY",
        "env_base_url": "CHATGLM_BASE_URL",
    },
    "custom": {
        "label": "Custom",
        "base_url": "",
        "models": [],
        "default_model": "",
        "env_key": "OPENAI_API_KEY",
        "env_base_url": "OPENAI_BASE_URL",
    },
}


def deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def _read_main_profile(path: Path) -> dict[str, object]:
    """读取配置文件中的 main profile；文件缺失或损坏时返回空字典。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    provider = data.get("provider")
    profiles = provider.get("model_profiles") if isinstance(provider, dict) else None
    main_profile = profiles.get("main") if isinstance(profiles, dict) else None
    return main_profile if isinstance(main_profile, dict) else {}


def _check_config_has_api_key(path: Path) -> bool:
    """检查单个 JSON 配置文件中是否有 main profile 的 api_key。"""
    return bool(_read_main_profile(path).get("api_key"))


def has_auth_credential() -> bool:
    """检查是否已有有效 OAuth 凭据（auth 优先于 api）。"""
    from cade.harness.auth.manager import AuthManager

    return AuthManager().get_valid_credential("openai-codex") is not None


def has_api_key(project_root: Path) -> bool:
    """检查配置文件、.env 或环境变量中是否已配置 API key。"""
    if _check_config_has_api_key(project_root / CONFIG_FILENAME):
        return True
    if _check_config_has_api_key(Path.home() / ".cade" / "settings.json"):
        return True

    env_paths = [
        project_root / ".env",
        project_root / "cade" / ".env",
    ]
    for env_path in env_paths:
        env = dotenv_values(env_path)
        if any(env.get(key) for key in API_KEY_ENV_NAMES):
            return True

    return any(os.environ.get(key) for key in API_KEY_ENV_NAMES)


def has_valid_config(project_root: Path) -> bool:
    """检查是否已有可用凭据：auth 优先，auth/api 任一存在即放行。"""
    return has_auth_credential() or has_api_key(project_root)


def prompt_login_method() -> str | None:
    """询问初始登录方式，返回 'auth'、'api' 或 None（用户取消）。"""
    choice = questionary.select(
        "No credentials found. Choose how to sign in:",
        choices=[LOGIN_CHOICE_AUTH, LOGIN_CHOICE_API],
        default=LOGIN_CHOICE_AUTH,
    ).ask()
    if choice is None:
        return None
    return "auth" if choice == LOGIN_CHOICE_AUTH else "api"


def _resolve_transport(provider_key: str) -> str:
    """映射 provider key 到 transport 名称。"""
    transport_map = {
        "openai": "openai_chat",
        "deepseek": "deepseek_chat",
        "mimo": "mimo_chat",
        "chatglm": "chatglm_chat",
        "custom": "openai_chat",
    }
    return transport_map.get(provider_key, "openai_chat")


def _select_provider() -> tuple[str, Any] | None:
    """交互式选择 LLM provider。返回 (key, preset) 或 None（取消）。"""
    choices = {preset["label"]: key for key, preset in PROVIDER_PRESETS.items()}
    provider_label = questionary.select("Select provider:", choices=list(choices)).ask()
    if provider_label is None:
        return None
    provider_key = choices[provider_label]
    return provider_key, PROVIDER_PRESETS[provider_key]


def _prompt_api_key(preset: dict[str, Any]) -> str | None:
    """交互式输入 API key。返回 key 或 None（取消）。"""
    env_key = preset["env_key"]
    env_val = os.environ.get(env_key) or ""
    api_key = questionary.text(
        "API Key:", default=env_val[:16] if env_val else "sk-"
    ).ask()
    if api_key is None:
        return None
    if not api_key:
        api_key = env_val
    return api_key


def _prompt_base_url(preset: dict[str, Any]) -> str | None:
    """交互式输入 Base URL。返回 URL 或 None（取消）。"""
    default_base_url = preset["base_url"]
    env_base_url = os.environ.get(preset["env_base_url"], "")
    base_url = questionary.text(
        "Base URL:", default=env_base_url or default_base_url
    ).ask()
    if base_url is None:
        return None
    if not base_url:
        base_url = env_base_url or default_base_url
    return base_url


def _prompt_model(preset: dict[str, Any]) -> str | None:
    """交互式选择模型。返回模型名或 None（取消）。"""
    if not preset["models"]:
        model = questionary.text("Model name:").ask()
        if model is None:
            return None
        if not model:
            return preset["default_model"]
        return model

    model_default = preset["default_model"]
    model_choices = [*preset["models"], "Custom (enter name)"]
    model = questionary.select("Model:", choices=model_choices).ask()
    if model is None:
        return None
    if model == "Custom (enter name)":
        model = questionary.text("Model name:").ask()
        if model is None:
            return None
        if not model:
            model = model_default
    return model


def _prompt_thinking_config(
    transport: str,
    model: str,
) -> tuple[bool, str | None] | None:
    """交互式配置 thinking 开关和 effort 级别。返回 (thinking, effort) 或 None（取消）。"""
    thinking_choice = questionary.select(
        "Thinking:", choices=["enabled", "disabled"], default="enabled"
    ).ask()
    if thinking_choice is None:
        return None
    thinking = thinking_choice == "enabled"
    reasoning_effort: str | None = None
    if thinking and supports_reasoning_effort(transport):
        effort = questionary.select(
            "Reasoning effort:",
            choices=list(reasoning_effort_levels_for_transport(transport, model)),
            default="high",
        ).ask()
        if effort is None:
            return None
        reasoning_effort = effort
    return thinking, reasoning_effort


def _build_config_data(
    transport: str,
    model: str,
    base_url: str,
    api_key: str,
    thinking: bool,
    reasoning_effort: str | None,
) -> dict[str, Any]:
    """构造配置字典。"""
    config_data: dict[str, Any] = {
        "provider": {
            "model_profiles": {
                "main": {
                    "transport": transport,
                    "chat_model": model,
                    "base_url": base_url,
                    "api_key": api_key,
                    "thinking": thinking,
                }
            },
        }
    }
    if reasoning_effort is not None:
        config_data["provider"]["model_profiles"]["main"]["reasoning_effort"] = (
            reasoning_effort
        )
    return config_data


def _print_summary(
    preset_label: str,
    model: str,
    base_url: str,
    thinking: bool,
    reasoning_effort: str | None,
    api_key: str,
) -> None:
    """打印配置摘要。"""
    print()
    print("  Summary:")
    print(f"    Provider  : {preset_label}")
    print(f"    Model     : {model}")
    if base_url:
        print(f"    Base URL  : {base_url}")
    print(f"    Thinking  : {'enabled' if thinking else 'disabled'}")
    if reasoning_effort is not None:
        print(f"    Effort    : {reasoning_effort}")
    masked = f"{'*' * max(0, len(api_key) - 4)}{api_key[-4:] if api_key else '(empty)'}"
    print(f"    API Key   : {masked}")
    print()


def _save_config(merged: dict[str, Any], config_path: Path) -> None:
    """将合并后的配置写入文件。"""
    config_path.write_text(
        json.dumps(merged, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _load_existing_config(config_path: Path) -> dict[str, Any]:
    """加载已有的配置文件，不存在或损坏时返回空字典。"""
    if not config_path.exists():
        return {}
    try:
        return json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def run_setup_wizard(project_root: Path) -> tuple[str, Path | None]:
    """首次启动配置向导。返回状态和临时配置路径。"""
    print()
    print("=" * 60)
    print("  Welcome to Cade - AI Coding Agent")
    print("=" * 60)
    print()
    print("No API key configured. Let's set up your LLM provider.")
    print()

    provider_result = _select_provider()
    if provider_result is None:
        return ("cancelled", None)
    provider_key, preset = provider_result

    print()
    print(f"  Provider: {preset['label']}")

    api_key = _prompt_api_key(preset)
    if api_key is None:
        return ("cancelled", None)

    base_url = _prompt_base_url(preset)
    if base_url is None:
        return ("cancelled", None)

    model = _prompt_model(preset)
    if model is None:
        return ("cancelled", None)

    transport = _resolve_transport(provider_key)

    thinking_result = _prompt_thinking_config(transport, model)
    if thinking_result is None:
        return ("cancelled", None)
    thinking, reasoning_effort = thinking_result

    _print_summary(
        preset["label"], model, base_url, thinking, reasoning_effort, api_key
    )

    save_choice = questionary.select(
        "Save this configuration?",
        choices=[
            "Global default (~/.cade/settings.json, recommended)",
            f"Current project only ({CONFIG_FILENAME})",
            "Don't save (temporary configuration)",
        ],
        default="Global default (~/.cade/settings.json, recommended)",
    ).ask()
    if save_choice is None:
        return ("cancelled", None)

    config_data = _build_config_data(
        transport,
        model,
        base_url,
        api_key,
        thinking,
        reasoning_effort,
    )

    if "Global default" in save_choice:
        global_path = Path.home() / ".cade" / "settings.json"
        global_path.parent.mkdir(parents=True, exist_ok=True)
        existing = _load_existing_config(global_path)
        merged = deep_merge(existing, config_data)
        _save_config(merged, global_path)
        print(f"  Configuration saved globally to {global_path}")
        print()
        return ("saved", None)
    elif "Current project" in save_choice:
        config_path = project_root / CONFIG_FILENAME
        existing = _load_existing_config(config_path)
        merged = deep_merge(existing, config_data)
        _save_config(merged, config_path)
        print(f"  Configuration saved to {CONFIG_FILENAME}")
        print()
        return ("saved", None)
    else:
        fd, tmp_path = tempfile.mkstemp(suffix=".json", prefix="cade_config_")
        os.close(fd)
        _save_config(config_data, Path(tmp_path))
        print("  Running with temporary configuration (not saved).")
        print()
        return ("no_save", Path(tmp_path))
