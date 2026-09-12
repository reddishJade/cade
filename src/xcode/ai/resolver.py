"""模型与 Transport 解析器（单一事实源）。

收敛别名规范化、Provider 推断、Transport 推断与默认 Base URL 解析。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Final

# 别名映射：不区分大小写，映射到规范 model ID
MODEL_ALIASES: Final[dict[str, str]] = {
    "codex": "gpt-5.3-codex",
    "openai-codex": "gpt-5.3-codex",
    "gpt-5.6": "gpt-5.6-sol",
}

# Provider 别名映射
PROVIDER_ALIASES: Final[dict[str, str]] = {
    "codex": "openai",
    "openai-codex": "openai",
    "glm": "chatglm",
}

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

# 支持 Codex (ChatGPT Plus/Pro OAuth) 认证的模型集合与过滤规则
CODEX_EXPLICIT_MODELS: Final[frozenset[str]] = frozenset(
    {
        "gpt-5.3-codex",
        "gpt-5.2-codex",
        "gpt-5.1-codex",
        "gpt-5-codex",
        "codex-mini-latest",
        "gpt-6-astra",
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.5",
        "gpt-5.4",
        "chat-latest",
    }
)

CODEX_EXCLUDED_SUBSTRINGS: Final[tuple[str, ...]] = ("mini", "nano", "4o")


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
        return MODEL_ALIASES.get(stripped.lower(), stripped)

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

        from xcode.ai.models import _MODELS

        for p_name, p_models in _MODELS.items():
            if resolved_name in p_models:
                return p_name

        return None

    @classmethod
    def is_codex_supported(cls, model: str) -> bool:
        """判定指定模型是否支持通过 ChatGPT Plus/Pro OAuth (openai-codex) 访问。"""
        resolved = cls.resolve_alias(model).lower()
        if resolved in CODEX_EXPLICIT_MODELS:
            return True
        if resolved.startswith(("gpt-", "codex-", "chat-", "o1", "o3", "o4")):
            return not any(sub in resolved for sub in CODEX_EXCLUDED_SUBSTRINGS)
        return False

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
        has_oauth: bool = False,
        has_api_key: bool = False,
        fallback_transport: str | None = None,
    ) -> str:
        """推断最合适的 transport。"""
        resolved_model = cls.resolve_alias(model)
        m_lower = model.strip().lower()
        norm_provider = cls.normalize_provider(provider) or cls.infer_provider(
            resolved_model
        )

        if norm_provider == "openai" or (provider in ("codex", "openai-codex")):
            if (
                m_lower in ("codex", "openai-codex")
                or has_oauth
                or (not has_api_key and cls.is_codex_supported(resolved_model))
            ):
                return "openai_codex"
            return fallback_transport or "openai_chat"

        if norm_provider == "deepseek":
            return "deepseek_chat"
        if norm_provider == "chatglm":
            return "chatglm_chat"
        if norm_provider == "mimo":
            return "mimo_chat"

        return fallback_transport or "openai_chat"

    @classmethod
    def resolve(
        cls,
        model_or_alias: str,
        provider: str | None = None,
        transport: str | None = None,
        *,
        has_oauth: bool = False,
        has_api_key: bool = False,
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
                has_oauth=has_oauth,
                has_api_key=has_api_key,
                fallback_transport=fallback_transport,
            )

        default_base_url = cls.get_default_base_url(resolved_transport)

        return ModelResolution(
            model=resolved_model,
            provider=norm_provider,
            transport=resolved_transport,
            default_base_url=default_base_url,
        )
