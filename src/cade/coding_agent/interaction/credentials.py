from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import dotenv_values

CONFIG_FILENAME = "cade.config.json"

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
