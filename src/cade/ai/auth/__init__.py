"""AI Provider 认证协议、凭据类型与解析。"""

from .openai_codex import OpenAICodexAuthProvider
from .registry import AUTH_PROVIDER_REGISTRY, get_auth_provider
from .resolver import CredentialResolver
from .types import AuthCredential, AuthProvider, CredentialStore

__all__ = [
    "AUTH_PROVIDER_REGISTRY",
    "AuthCredential",
    "AuthProvider",
    "CredentialResolver",
    "CredentialStore",
    "OpenAICodexAuthProvider",
    "get_auth_provider",
]
