"""检查已选择 provider 的凭据，不用其他账号改变模型选择。"""

from __future__ import annotations

from pathlib import Path

from cade.ai.providers.registry import get_environment_api_key
from cade.ai.resolver import PROVIDER_TRANSPORTS, ModelResolver
from cade.harness.auth.manager import AuthManager
from cade.harness.config import discover_runtime_config

CONFIG_FILENAME = "cade.config.json"


def has_valid_config(project_root: Path, config_path: Path | None = None) -> bool:
    """只检查默认 provider 的认证条件，连接是否成功由运行确认。"""
    config = discover_runtime_config(project_root, config_path)
    selected = (
        ModelResolver.normalize_provider(config.default_provider)
        or config.default_provider
    )
    connection = config.provider.connections.get(selected)
    transport = (
        connection.transport if connection else PROVIDER_TRANSPORTS.get(selected)
    )
    credential = AuthManager().get_valid_credential(selected)
    if transport == "openai_codex":
        return bool(credential and credential.type == "oauth" and credential.access)
    if credential and credential.type == "api_key" and credential.access:
        return True
    return bool(
        get_environment_api_key(
            transport or "",
            (project_root / ".env", project_root / "cade" / ".env"),
            connection.api_key_env if connection else None,
        )
    )
