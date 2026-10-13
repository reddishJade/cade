"""检查系统已安装的外部工具（如 ripgrep）。"""

from __future__ import annotations

import logging
import shutil

logger = logging.getLogger("cade.coding_agent.tools.tools_manager")

_TOOLS: dict[str, tuple[str, ...]] = {
    "fd": ("fd", "fdfind"),
    "rg": ("rg",),
}


def get_tool_path(tool: str) -> str | None:
    """检查工具是否在系统 PATH 中可用。"""
    for name in _TOOLS.get(tool, ()):
        found = shutil.which(name)
        if found:
            return found
    return None


def ensure_tool(tool: str, silent: bool = False) -> str | None:
    """确保工具可用。

    检查工具是否在系统 PATH 中，返回工具路径，若不可用则返回 None。
    """
    existing = get_tool_path(tool)
    if existing:
        return existing

    if tool in _TOOLS and not silent:
        logger.warning("%s not found in PATH. Please install it manually.", tool)

    return None
