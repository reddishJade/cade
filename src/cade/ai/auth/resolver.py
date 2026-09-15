"""请求前凭据解析与自动刷新。"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping

import httpx

from .types import AuthCredential, AuthProvider, CredentialStore

_LOGGER = logging.getLogger(__name__)
_REFRESH_WINDOW_SECONDS = 300


class CredentialResolver:
    """使用 Provider 认证实现解析可用凭据。"""

    def __init__(
        self,
        store: CredentialStore,
        providers: Mapping[str, AuthProvider],
    ) -> None:
        self.store = store
        self.providers = providers

    def resolve(self, provider: str) -> AuthCredential | None:
        """返回有效凭据，并在临近过期时尝试刷新。"""
        credential = self.store.get(provider)
        if credential is None:
            return None

        now = int(time.time())
        expires_soon = bool(
            credential.expires and credential.expires - now < _REFRESH_WINDOW_SECONDS
        )
        auth_provider = self.providers.get(provider)
        if not expires_soon or not credential.refresh or auth_provider is None:
            return credential

        try:
            _LOGGER.info("OAuth token for %s is near expiry, refreshing...", provider)
            refreshed = auth_provider.refresh(credential)
            self.store.save(refreshed)
            return refreshed
        except (
            RuntimeError,
            TimeoutError,
            OSError,
            ValueError,
            httpx.HTTPError,
        ) as exc:
            _LOGGER.warning("Failed to refresh token for %s: %s", provider, exc)
            return credential
