from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
    from cade.ai.providers.registry import get_config_value
    from cade.harness.auth.manager import AuthManager

    entries: list[AvailableModelEntry] = []
    seen: set[tuple[str, str]] = set()

    def add_entry(model: str, transport: str, provider: str, label: str) -> None:
        key = (model, transport)
        if key not in seen:
            seen.add(key)
            entries.append(
                AvailableModelEntry(
                    model=model,
                    transport=transport,
                    provider=provider,
                    source_label=label,
                )
            )

    env_files: tuple[Path, ...] = getattr(app, "_env_files", ())
    model_profiles: dict[str, Any] | None = getattr(app, "_model_profiles", None)

    # 1. 检查已认证的 OAuth 凭据 (如 openai-codex / ChatGPT OAuth)
    codex_cred = AuthManager().get_valid_credential("openai-codex")
    if codex_cred and codex_cred.access:
        for m in get_codex_models():
            add_entry(m.id, "openai_codex", "openai-codex", "[codex]")

    # 2. 检查环境变量及 .env 中的各 Provider API Key
    # DeepSeek
    if get_config_value("DEEPSEEK_API_KEY", env_files):
        for m in get_models("deepseek"):
            add_entry(m.id, "deepseek_chat", "deepseek", "[deepseek]")

    # OpenAI API Key
    if get_config_value("OPENAI_API_KEY", env_files):
        for m in get_models("openai"):
            add_entry(m.id, "openai_chat", "openai", "[openai]")

    # ChatGLM (智谱)
    if any(
        get_config_value(k, env_files)
        for k in ("CHATGLM_API_KEY", "ZHIPUAI_API_KEY", "BIGMODEL_API_KEY")
    ):
        for m in get_models("chatglm"):
            add_entry(m.id, "chatglm_chat", "chatglm", "[chatglm]")

    # Xiaomi MiMo
    if get_config_value("MIMO_API_KEY", env_files):
        for m in get_models("mimo"):
            add_entry(m.id, "mimo_chat", "mimo", "[mimo]")

    # 3. 只补充 main profile 自身配置的凭据；其他 profile 不能切换主模型
    if model_profiles:
        pconfig = model_profiles.get("main")
        t = getattr(pconfig, "transport", None)
        m = getattr(pconfig, "chat_model", None)
        k = getattr(pconfig, "api_key", None)
        if m and t and (k or (m, t) in seen):
            provider = t.removesuffix("_chat").removeprefix("openai_")
            add_entry(m, t, provider, f"[{provider}]")

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
                    cur_transport.removesuffix("_chat"),
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
