"""Provider 注册表与工厂。

职责：
1. 维护 transport → provider class 的映射
2. 从已解析的模型配置构建单个 provider 实例
3. 解析 API key（环境变量回退）
"""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

from dotenv import dotenv_values

from cade.ai.providers.base import ModelProvider
from cade.ai.resolver import ModelResolver
from cade.ai.types import ProviderConfig

from .chatglm import ChatGLMProvider
from .deepseek import DeepSeekProvider
from .mimo import MiMoProvider
from .openai import OpenAIChatProvider
from .responses import OpenAICodexResponsesProvider, OpenAIResponsesProvider

# ── 注册表 ──

PROVIDER_REGISTRY: dict[str, type] = {
    "openai_chat": OpenAIChatProvider,
    "openai_responses": OpenAIResponsesProvider,
    "openai_codex": OpenAICodexResponsesProvider,
    "chatglm_chat": ChatGLMProvider,
    "deepseek_chat": DeepSeekProvider,
    "mimo_chat": MiMoProvider,
    # custom：任意 OpenAI 协议网关（自定义 base_url + api_key）
    "custom": OpenAIChatProvider,
}


# ── 环境变量工具 ──


def load_env_file(path: Path) -> dict[str, str | None]:
    return dotenv_values(path)


def get_config_value(name: str, env_files: tuple[Path, ...] = ()) -> str | None:
    if value := os.environ.get(name):
        return value
    for env_file in env_files:
        values = load_env_file(env_file)
        if value := values.get(name):
            return value
    return None


# ── Factory ──


_PROVIDER_ENV_VARS: dict[str, tuple[str, ...]] = {
    "chatglm_chat": ("CHATGLM_API_KEY", "ZHIPUAI_API_KEY", "BIGMODEL_API_KEY"),
    "deepseek_chat": ("DEEPSEEK_API_KEY",),
    "mimo_chat": ("MIMO_API_KEY",),
    "openai_chat": ("OPENAI_API_KEY",),
    "openai_responses": ("OPENAI_API_KEY",),
    "custom": ("API_KEY",),
}


def get_environment_api_key(
    transport: str,
    env_files: tuple[Path, ...] = (),
    api_key_env: str | None = None,
) -> str | None:
    """只读取所选 provider 的环境凭据，不借用其他服务的 key。"""
    names = (api_key_env,) if api_key_env else _PROVIDER_ENV_VARS.get(transport, ())
    for name in names:
        if value := get_config_value(name, env_files):
            return value
    return None


def build_provider(config: ProviderConfig) -> ModelProvider:
    """从已解析配置直接构造适配器，不另建配置对象。"""
    if not config.api_key:
        raise RuntimeError("Missing credentials for model profile")
    provider_cls = PROVIDER_REGISTRY.get(config.transport)
    if provider_cls is None:
        raise ValueError(
            f"Unknown transport '{config.transport}'. "
            f"Available: {', '.join(PROVIDER_REGISTRY)}"
        )
    if not config.base_url:
        config = replace(
            config, base_url=ModelResolver.get_default_base_url(config.transport)
        )
    return provider_cls(config)
