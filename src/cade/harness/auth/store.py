"""Cade 认证凭据持久化存储管理器。

负责安全读写 ~/.cade/auth.json。
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from cade.ai.auth.types import AuthCredential

DEFAULT_AUTH_FILE = Path.home() / ".cade" / "auth.json"


class AuthStore:
    """Cade 本地凭据存储管理器。"""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DEFAULT_AUTH_FILE

    def load_all(self) -> dict[str, AuthCredential]:
        """加载所有已保存的凭据。"""
        if not self.path.exists():
            return {}
        try:
            content = self.path.read_text(encoding="utf-8")
            raw_data = json.loads(content)
            if not isinstance(raw_data, dict):
                return {}
            result: dict[str, AuthCredential] = {}
            for provider, val in raw_data.items():
                if isinstance(val, dict):
                    result[provider] = AuthCredential.from_dict(val, provider=provider)
            return result
        except (OSError, json.JSONDecodeError):
            return {}

    def get(self, provider: str) -> AuthCredential | None:
        """获取指定 Provider 的凭据。"""
        return self.load_all().get(provider)

    def save(self, credential: AuthCredential) -> None:
        """保存或更新单个 Provider 的凭据。"""
        all_creds = self.load_all()
        all_creds[credential.provider] = credential

        self.path.parent.mkdir(parents=True, exist_ok=True)
        dump_data = {k: v.to_dict() for k, v in all_creds.items()}
        self.path.write_text(
            json.dumps(dump_data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        self._ensure_secure_permissions()

    def delete(self, provider: str) -> bool:
        """删除指定 Provider 的凭据，返回是否成功删除。"""
        all_creds = self.load_all()
        if provider not in all_creds:
            return False
        del all_creds[provider]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        dump_data = {k: v.to_dict() for k, v in all_creds.items()}
        self.path.write_text(
            json.dumps(dump_data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        self._ensure_secure_permissions()
        return True

    def _ensure_secure_permissions(self) -> None:
        """在 POSIX 系统上设置 0600 文件权限以保障私密凭据安全。"""
        if getattr(os, "name", "") == "posix" and self.path.exists():
            try:
                self.path.chmod(stat.S_IRUSR | stat.S_IWUSR)
            except OSError:
                pass
