"""把缓存结果渲染为固定宽度的文本行。"""

from __future__ import annotations

WIDTH = 72


def truncate(text: str, width: int = WIDTH) -> str:
    """把文本裁剪到指定宽度，超出部分用省略号标记。"""
    if len(text) <= width:
        return text
    return text[: width - 1].rstrip() + "…"
