"""Xiaomi MiMo provider（兼容 OpenAI Chat API，带 reasoning_content 支持）。"""

from __future__ import annotations

from ._compat import OpenAICompatProvider

MIMO_BASE_URL = "https://api.xiaomimimo.com/v1"


class MiMoProvider(OpenAICompatProvider):
    """Xiaomi MiMo Chat API 适配。"""

    transport = "mimo_chat"
