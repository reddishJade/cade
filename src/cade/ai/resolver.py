"""模型与 Transport 解析器（单一事实源）。

收敛别名规范化、Provider 推断、Transport 推断与默认 Base URL 解析。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Final

from cade.ai.models import get_codex_models, normalize_model_id

# 别名映射：不区分大小写，映射到规范 model ID
MODEL_ALIASES: Final[dict[str, str]] = {
    "codex": "gpt-5.6-luna",
    "openai-codex": "gpt-5.6-luna",
    "gpt-6": "gpt-6-sol",
    "gpt-5.6": "gpt-5.6-sol",
}

# Provider 别名映射
PROVIDER_ALIASES: Final[dict[str, str]] = {
    "codex": "openai-codex",
    "openai-codex": "openai-codex",
    "glm": "chatglm",
}

PROVIDER_TRANSPORTS: Final[dict[str, str]] = {
    "openai": "openai_responses",
    "openai-codex": "openai_codex",
    "deepseek": "deepseek_chat",
    "chatglm": "chatglm_chat",
    "mimo": "mimo_chat",
    "custom": "custom",
}


def provider_for_transport(transport: str) -> str:
    """把传输协议映射到独立的认证提供方。"""
    if transport == "openai_chat":
        return "openai"
    for provider, candidate in PROVIDER_TRANSPORTS.items():
        if candidate == transport:
            return provider
    raise ValueError(f"Unknown transport: {transport}")


# 默认 Base URL
DEFAULT_BASE_URLS: Final[dict[str, str]] = {
    "openai_codex": "https://chatgpt.com/backend-api",
    "openai_chat": "https://api.openai.com/v1",
    "openai_responses": "https://api.openai.com/v1",
    "deepseek_chat": "https://api.deepseek.com",
    "chatglm_chat": "https://open.bigmodel.cn/api/paas/v4/",
    "mimo_chat": "https://api.xiaomimimo.com/v1",
}

# 默认环境变量覆盖 Base URL
BASE_URL_ENV_VARS: Final[dict[str, str]] = {
    "deepseek_chat": "DEEPSEEK_BASE_URL",
    "chatglm_chat": "CHATGLM_BASE_URL",
    "mimo_chat": "MIMO_BASE_URL",
    "openai_chat": "OPENAI_BASE_URL",
    "openai_responses": "OPENAI_BASE_URL",
}

# ChatGPT 登录的 Codex 只接受当前模型清单，不再按名称前缀猜测可用性。
CODEX_EXPLICIT_MODELS: Final[frozenset[str]] = frozenset(
    model.id for model in get_codex_models()
)


@dataclass(frozen=True)
class ModelResolution:
    """解析后的模型与传输配置。"""

    model: str
    provider: str
    transport: str
    default_base_url: str


class ModelResolver:
    """集中式模型与 Transport 解析领域服务。"""

    @staticmethod
    def resolve_alias(model: str) -> str:
        """规范化模型别名，若无别名则返回原名。"""
        stripped = model.strip()
        return MODEL_ALIASES.get(stripped.lower()) or normalize_model_id(stripped)

    @staticmethod
    def normalize_provider(provider: str | None) -> str | None:
        """规范化 provider 别名。"""
        if not provider:
            return None
        p_lower = provider.strip().lower()
        return PROVIDER_ALIASES.get(p_lower, p_lower)

    @classmethod
    def infer_provider(cls, model: str) -> str | None:
        """根据模型名称或前缀推断其归属 Provider。"""
        resolved_name = cls.resolve_alias(model).lower()

        if resolved_name in ("codex", "openai-codex") or resolved_name.startswith(
            ("gpt-", "codex-", "chat-", "o1", "o3", "o4")
        ):
            return "openai"
        if resolved_name.startswith("deepseek"):
            return "deepseek"
        if resolved_name.startswith("glm-"):
            return "chatglm"
        if resolved_name.startswith("mimo-"):
            return "mimo"

        from cade.ai.models import _MODELS

        for p_name, p_models in _MODELS.items():
            if resolved_name in p_models:
                return p_name

        return None

    @classmethod
    def is_codex_supported(cls, model: str) -> bool:
        """判定指定模型是否支持通过 ChatGPT Plus/Pro OAuth (openai-codex) 访问。"""
        resolved = cls.resolve_alias(model).lower()
        return resolved in CODEX_EXPLICIT_MODELS

    @staticmethod
    def get_default_base_url(transport: str) -> str:
        """获取指定 transport 的默认 Base URL（优先读取环境变量覆盖）。"""
        env_var = BASE_URL_ENV_VARS.get(transport)
        if env_var and os.environ.get(env_var):
            return os.environ[env_var]
        return DEFAULT_BASE_URLS.get(transport, "")

    @classmethod
    def infer_transport(
        cls,
        model: str,
        provider: str | None = None,
        *,
        fallback_transport: str | None = None,
    ) -> str:
        """推断最合适的 transport。"""
        raw_model = model.strip().lower()
        selected = cls.normalize_provider(provider) or (
            "openai-codex"
            if raw_model in ("codex", "openai-codex")
            else cls.infer_provider(model)
        )
        if selected in PROVIDER_TRANSPORTS:
            return PROVIDER_TRANSPORTS[selected]
        return fallback_transport or "openai_responses"

    @classmethod
    def resolve(
        cls,
        model_or_alias: str,
        provider: str | None = None,
        transport: str | None = None,
        *,
        fallback_transport: str | None = None,
    ) -> ModelResolution:
        """一站式解析模型名称、Provider、Transport 和 Base URL。"""
        raw_name = model_or_alias.strip()
        extracted_provider = provider
        if "/" in raw_name:
            p_prefix, m_part = raw_name.split("/", 1)
            extracted_provider = provider or p_prefix.strip()
            raw_name = m_part.strip()

        resolved_model = cls.resolve_alias(raw_name)
        if extracted_provider is None:
            if (
                raw_name.lower() in ("codex", "openai-codex")
                or fallback_transport == "openai_codex"
                and cls.infer_provider(resolved_model) == "openai"
            ):
                extracted_provider = "openai-codex"
            elif cls.infer_provider(resolved_model) is None and fallback_transport:
                extracted_provider = provider_for_transport(fallback_transport)
        norm_provider = (
            cls.normalize_provider(extracted_provider)
            or cls.infer_provider(resolved_model)
            or "openai"
        )

        if transport:
            resolved_transport = transport
        else:
            resolved_transport = cls.infer_transport(
                model=resolved_model,
                provider=norm_provider,
                fallback_transport=fallback_transport,
            )

        default_base_url = cls.get_default_base_url(resolved_transport)

        return ModelResolution(
            model=resolved_model,
            provider=norm_provider,
            transport=resolved_transport,
            default_base_url=default_base_url,
        )
