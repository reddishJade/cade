"""AI Provider 认证实现注册表。"""

from __future__ import annotations

from collections.abc import Mapping

from .openai_codex import OpenAICodexAuthProvider
from .types import AuthProvider

_OPENAI_CODEX_AUTH = OpenAICodexAuthProvider()

AUTH_PROVIDER_REGISTRY: Mapping[str, AuthProvider] = {
    _OPENAI_CODEX_AUTH.id: _OPENAI_CODEX_AUTH,
}


def get_auth_provider(provider: str) -> AuthProvider | None:
    """按 Provider ID 返回专有认证实现。"""
    return AUTH_PROVIDER_REGISTRY.get(provider)
