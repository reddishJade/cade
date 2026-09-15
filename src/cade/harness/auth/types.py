"""Cade 认证凭据类型定义。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

CredentialType = Literal["oauth", "api_key"]


@dataclass
class AuthCredential:
    """单个 Provider 的认证凭据。

    Attributes:
        type: 凭据类型，如 "oauth" 或 "api_key"。
        access: 访问令牌或 API Key。
        refresh: 刷新令牌（OAuth 流程专用）。
        expires: 过期时间戳（秒）。
        account_id: 账户标识（如 ChatGPT 组织/账户 ID）。
        provider: 所属服务提供方（如 "openai-codex"）。
        extra: 其他扩展元数据。
    """

    type: CredentialType = "oauth"
    access: str = ""
    refresh: str | None = None
    expires: int | None = None
    account_id: str | None = None
    provider: str = ""
    extra: dict[str, Any] | None = None

    def is_expired(self) -> bool:
        """检查凭据是否已经过期。"""
        import time

        if not self.expires:
            return False
        return self.expires <= int(time.time())

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if not self.extra:
            data.pop("extra", None)
        return {k: v for k, v in data.items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict[str, Any], provider: str = "") -> AuthCredential:
        return cls(
            type=data.get("type", "oauth"),
            access=data.get("access", ""),
            refresh=data.get("refresh"),
            expires=data.get("expires"),
            account_id=data.get("account_id") or data.get("accountId"),
            provider=data.get("provider", provider),
            extra=data.get("extra"),
        )
