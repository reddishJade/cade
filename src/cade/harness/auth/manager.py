"""Cade 凭据管理入口。"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

import httpx

from .openai_codex import (
    login_openai_codex_browser,
    login_openai_codex_device_code,
    refresh_openai_codex_token,
)
from .store import AuthStore
from .types import AuthCredential

_LOGGER = logging.getLogger(__name__)


class AuthManager:
    """Cade 统一凭据生命周期与刷新管理器。"""

    def __init__(self, store: AuthStore | None = None) -> None:
        self.store = store or AuthStore()

    def get_valid_credential(
        self,
        provider: str = "openai-codex",
    ) -> AuthCredential | None:
        """获取有效凭据；若即将过期且有 refresh token，将自动刷新。"""
        cred = self.store.get(provider)
        if not cred:
            return None

        now = int(time.time())
        # 若在 5 分钟之内过期，尝试自动刷新
        if cred.expires and (cred.expires - now < 300) and cred.refresh:
            try:
                _LOGGER.info(
                    "OAuth token for %s is near expiry, refreshing...", provider
                )
                if provider == "openai-codex":
                    refreshed = refresh_openai_codex_token(cred.refresh)
                    self.store.save(refreshed)
                    return refreshed
            except (RuntimeError, TimeoutError, OSError, httpx.HTTPError) as exc:
                _LOGGER.warning("Failed to refresh token for %s: %s", provider, exc)

        return cred

    def login(
        self,
        provider: str = "openai-codex",
        *,
        method: str = "browser",
        notify_callback: Callable[..., None] | None = None,
    ) -> AuthCredential:
        """执行 Provider 登录并写入凭据存储。"""
        if provider == "openai-codex":
            if method == "device_code":
                if not notify_callback:
                    raise ValueError("Device code 登录需要提供 notify_callback")
                cred = login_openai_codex_device_code(notify_callback=notify_callback)
            else:
                cred = login_openai_codex_browser(
                    notify_callback=notify_callback,
                )
            self.store.save(cred)
            return cred
        raise ValueError(f"暂不支持的认证提供方: {provider}")

    def logout(self, provider: str = "openai-codex") -> bool:
        """登出并清理 Provider 凭据。"""
        return self.store.delete(provider)

    def list_accounts(self) -> list[dict[str, object]]:
        """列出所有已配置的账号状态（不泄露完整 token）。"""
        creds = self.store.load_all()
        now = int(time.time())
        result: list[dict[str, object]] = []
        for name, c in creds.items():
            is_expired = bool(c.expires and c.expires <= now)
            result.append(
                {
                    "provider": name,
                    "type": c.type,
                    "account_id": c.account_id,
                    "has_refresh": bool(c.refresh),
                    "expired": is_expired,
                    "expires_in_seconds": (
                        c.expires - now if c.expires and not is_expired else 0
                    ),
                }
            )
        return result
