"""回合工作状态的纯展示辅助。"""

from __future__ import annotations

import time

# 只表示请求仍在进行，不暗示模型正在输出私有思维链。
WORKING_FRAMES: tuple[str, ...] = (
    "⠋",
    "⠙",
    "⠹",
    "⠸",
    "⠼",
    "⠴",
    "⠦",
    "⠧",
    "⠇",
    "⠏",
)


def working_status_text(now: float | None = None) -> str:
    """返回当前动画帧和非推理性质的工作提示。"""
    timestamp = time.perf_counter() if now is None else now
    frame = WORKING_FRAMES[int(timestamp * 10) % len(WORKING_FRAMES)]
    return f"{frame} Working..."
