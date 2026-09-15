"""Cade 凭据管理入口。"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping

from cade.ai.auth import (
    AUTH_PROVIDER_REGISTRY,
    AuthCredential,
    AuthProvider,
    CredentialResolver,
    CredentialStore,
)

from .store import AuthStore


class AuthManager:
    """编排登录、登出以及宿主凭据存储。"""

    def __init__(
        self,
        store: CredentialStore | None = None,
        providers: Mapping[str, AuthProvider] | None = None,
    ) -> None:
        self.store = store or AuthStore()
        self.providers = AUTH_PROVIDER_REGISTRY if providers is None else providers
        self.resolver = CredentialResolver(self.store, self.providers)

    @property
    def storage_location(self) -> str:
        """返回可供界面展示的宿主存储位置。"""
        path = getattr(self.store, "path", None)
        return str(path) if path is not None else "configured credential store"

    def get_valid_credential(
        self,
        provider: str = "openai-codex",
    ) -> AuthCredential | None:
        """通过 AI 认证解析器获取请求可用凭据。"""
        return self.resolver.resolve(provider)

    def login(
        self,
        provider: str = "openai-codex",
        *,
        method: str = "browser",
        notify_callback: Callable[..., None] | None = None,
    ) -> AuthCredential:
        """执行 Provider 登录并把结果写入宿主存储。"""
        auth_provider = self.providers.get(provider)
        if auth_provider is None:
            raise ValueError(f"暂不支持的认证提供方: {provider}")
        credential = auth_provider.login(
            method=method,
            notify_callback=notify_callback,
        )
        self.store.save(credential)
        return credential

    def logout(self, provider: str = "openai-codex") -> bool:
        """登出并清理 Provider 凭据。"""
        return self.store.delete(provider)

    def list_accounts(self) -> list[dict[str, object]]:
        """列出所有已配置的账号状态（不泄露完整 token）。"""
        credentials = self.store.load_all()
        now = int(time.time())
        result: list[dict[str, object]] = []
        for name, credential in credentials.items():
            is_expired = bool(credential.expires and credential.expires <= now)
            result.append(
                {
                    "provider": name,
                    "type": credential.type,
                    "account_id": credential.account_id,
                    "has_refresh": bool(credential.refresh),
                    "expired": is_expired,
                    "expires_in_seconds": (
                        credential.expires - now
                        if credential.expires and not is_expired
                        else 0
                    ),
                }
            )
        return result
