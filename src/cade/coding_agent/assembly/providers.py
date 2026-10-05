"""先选择模型和连接，再解析该 provider 的凭据。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cade.ai.models import get_model_reasoning_efforts
from cade.ai.providers.registry import ModelProfileConfig, get_environment_api_key
from cade.ai.resolver import PROVIDER_TRANSPORTS, ModelResolver
from cade.harness.auth.manager import AuthManager
from cade.harness.config import CadeRuntimeConfig


def resolve_model_profile(
    config: CadeRuntimeConfig,
    provider: str,
    model: str,
    options: dict[str, Any],
    env_files: tuple[Path, ...],
) -> ModelProfileConfig:
    """认证只提供凭据，不改变已选择的 provider、模型或端点。"""
    selected = ModelResolver.normalize_provider(provider) or provider
    connection = config.provider.connections.get(selected)
    transport = (
        connection.transport if connection else PROVIDER_TRANSPORTS.get(selected)
    )
    if transport is None:
        raise ValueError(
            f"Unknown provider '{selected}'; configure provider.connections"
        )
    resolved_model = ModelResolver.resolve_alias(model)
    if transport == "openai_codex" and not ModelResolver.is_codex_supported(
        resolved_model
    ):
        raise ValueError(
            f"Model '{resolved_model}' is not available through openai-codex"
        )
    credential = AuthManager().get_valid_credential(selected)
    if transport == "openai_codex":
        if credential is None or credential.type != "oauth" or not credential.access:
            raise RuntimeError("Missing openai-codex login; run 'cade login'")
        key = credential.access
        account_id = credential.account_id
    else:
        key = (
            credential.access
            if credential is not None and credential.type == "api_key"
            else get_environment_api_key(
                transport, env_files, connection.api_key_env if connection else None
            )
        )
        account_id = None
        if not key:
            raise RuntimeError(
                f"Missing API key for provider '{selected}'; "
                "run 'cade login --method api_key' or set its API key environment variable"
            )
    effort = options.get("reasoning_effort")
    supported = get_model_reasoning_efforts(resolved_model)
    if effort is not None and supported and effort not in supported:
        raise ValueError(
            f"Model '{resolved_model}' does not support effort '{effort}'; "
            f"use: {', '.join(supported)}"
        )
    return ModelProfileConfig(
        provider=selected,
        transport=transport,
        chat_model=resolved_model,
        base_url=(connection.base_url if connection else "")
        or ModelResolver.get_default_base_url(transport),
        api_key=key,
        account_id=account_id,
        **options,
    )


def resolve_model_profiles(
    config: CadeRuntimeConfig, env_files: tuple[Path, ...]
) -> dict[str, ModelProfileConfig]:
    """展开角色覆盖，省略的角色继承主模型选择和请求选项。"""
    options = config.provider.options.model_dump()
    options["reasoning_effort"] = config.default_reasoning_effort
    main = resolve_model_profile(
        config, config.default_provider, config.default_model, options, env_files
    )
    profiles = {"main": main}
    for role, override in config.provider.model_profiles.items():
        fields = override.model_dump(exclude_unset=True)
        provider = fields.pop("provider", None) or config.default_provider
        model = fields.pop("model", None) or config.default_model
        profiles[role] = resolve_model_profile(
            config, provider, model, {**options, **fields}, env_files
        )
    profiles.setdefault("subagent", main)
    profiles.setdefault("fallback", main)
    return profiles
