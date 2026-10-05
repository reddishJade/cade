from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cade.ai.models import get_codex_models, get_models
from cade.ai.resolver import ModelResolver

from .reasoning_effort import reasoning_effort_levels_for_transport


@dataclass
class AvailableModelEntry:
    model: str
    transport: str
    provider: str
    source_label: str


def is_current_model_entry(
    entry: AvailableModelEntry,
    current_model: str,
    current_transport: str,
) -> bool:
    """判断模型条目是否对应当前运行中的主模型。"""
    return entry.model == current_model and (
        not current_transport or entry.transport == current_transport
    )


def get_available_model_entries(app: object) -> list[AvailableModelEntry]:
    """返回当前环境中所有具备已认证凭据或有效 API Key 的可用模型。

    注意：严格只返回已配置/已登录模型，未配置 API Key 或凭据的不予包含。
    """
    from cade.ai.providers.registry import get_environment_api_key
    from cade.ai.resolver import PROVIDER_TRANSPORTS
    from cade.harness.auth.manager import AuthManager
    from cade.harness.config import CadeRuntimeConfig

    entries: list[AvailableModelEntry] = []
    seen: set[tuple[str, str]] = set()

    def add_entry(model: str, transport: str, provider: str, label: str) -> None:
        key = (model, transport)
        if key not in seen:
            seen.add(key)
            entries.append(AvailableModelEntry(model, transport, provider, label))

    env_files: tuple[Path, ...] = getattr(app, "_env_files", ())
    config: CadeRuntimeConfig = (
        getattr(app, "_runtime_config", None) or CadeRuntimeConfig()
    )
    manager = AuthManager()
    providers = dict(PROVIDER_TRANSPORTS)
    providers.update(
        {name: c.transport for name, c in config.provider.connections.items()}
    )
    for provider, transport in providers.items():
        credential = manager.get_valid_credential(provider)
        connection = config.provider.connections.get(provider)
        if transport == "openai_codex":
            available = (
                credential is not None
                and credential.type == "oauth"
                and bool(credential.access)
            )
            models = get_codex_models()
        else:
            available = bool(
                (credential and credential.type == "api_key" and credential.access)
                or get_environment_api_key(
                    transport, env_files, connection.api_key_env if connection else None
                )
            )
            models = get_models(provider)
        if available:
            for model in models:
                add_entry(model.id, transport, provider, f"[{provider}]")

    # 4. 当前运行中的主模型
    info = dict(getattr(app, "get_model_info", dict)())
    cur_model = info.get("model", "")
    cur_transport = info.get("transport", "")
    if cur_model:
        current_key = (cur_model, cur_transport)
        current_is_codex = cur_transport == "openai_codex"
        current_is_supported = not current_is_codex or ModelResolver.is_codex_supported(
            cur_model
        )
        if current_key not in seen or not current_is_supported:
            entries[:] = [entry for entry in entries if entry.model != cur_model]
            seen.clear()
            seen.update((entry.model, entry.transport) for entry in entries)
            if current_is_supported:
                add_entry(
                    cur_model,
                    cur_transport,
                    info.get("provider", cur_transport.removesuffix("_chat")),
                    "[configured]",
                )

    return entries


def current_effort_options(app: object) -> tuple[str, ...]:
    """返回当前 active provider 支持的 reasoning effort 选项。"""
    agent = getattr(app, "agent", None)
    provider = getattr(agent, "provider", None) if agent else None
    provider = getattr(provider, "active_provider", provider)
    transport = getattr(provider, "transport", "") if provider else ""
    model = getattr(provider, "model", "") if provider else ""
    return reasoning_effort_levels_for_transport(transport, model)


def current_model_options(app: object) -> tuple[str, ...]:
    """返回所有当前具备认证凭据或 API Key 的可用模型 ID 列表。"""
    entries = get_available_model_entries(app)
    seen: set[str] = set()
    result: list[str] = []
    for entry in entries:
        if entry.model not in seen:
            seen.add(entry.model)
            result.append(entry.model)
    return tuple(result)
