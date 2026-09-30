"""把缓存中的报告渲染为固定宽度的文本行。"""

from __future__ import annotations

from collections.abc import Mapping

from src.cache import get, put
from src.render import truncate


def render_report(parameters: Mapping[str, str]) -> str:
    """返回缓存中的报告文本，未命中时生成并写入缓存。"""
    cached = get(parameters)
    if cached is not None:
        return cached
    joined = ", ".join(f"{name}={value}" for name, value in parameters.items())
    body = truncate(f"report: {joined}")
    put(parameters, body)
    return body
