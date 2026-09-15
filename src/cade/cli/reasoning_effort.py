"""推理 effort 相关的命令和配置辅助函数。"""

from __future__ import annotations

from collections.abc import Iterable

from cade.ai.models import get_model_reasoning_efforts

EFFORT_COMMAND_LEVELS: tuple[str, ...] = (
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
)

OPENAI_CHAT_EFFORT_LEVELS: tuple[str, ...] = EFFORT_COMMAND_LEVELS
DEEPSEEK_CHAT_EFFORT_LEVELS: tuple[str, ...] = ("off", "high", "max")

SUPPORTED_EFFORT_TRANSPORTS: frozenset[str] = frozenset(
    {
        "openai_chat",
        "openai_responses",
        "openai_codex",
        "deepseek_chat",
        "custom",
    }
)


def supports_reasoning_effort(transport: str) -> bool:
    """判断指定 transport 是否支持 reasoning_effort。"""
    return transport in SUPPORTED_EFFORT_TRANSPORTS


def reasoning_effort_levels_for_transport(
    transport: str,
    model: str | None = None,
) -> tuple[str, ...]:
    """返回指定 transport 和模型可用的 effort 选项。"""
    if transport == "deepseek_chat":
        return DEEPSEEK_CHAT_EFFORT_LEVELS
    if transport in {"openai_chat", "openai_responses", "openai_codex"} and model:
        model_levels = get_model_reasoning_efforts(model)
        if model_levels:
            return model_levels
    # 非 openai 协议（deepseek/custom）的档位映射由 provider 或网关处理，
    # 界面统一展示 openai 档位
    if transport in SUPPORTED_EFFORT_TRANSPORTS:
        return OPENAI_CHAT_EFFORT_LEVELS
    return ()


def normalize_reasoning_effort_options(
    options: Iterable[str] | None,
) -> tuple[str, ...]:
    """将 effort 选项标准化为去重后的元组。"""
    if options is None:
        return ()
    normalized: list[str] = []
    for option in options:
        text = option.strip().lower()
        if text and text not in normalized:
            normalized.append(text)
    return tuple(normalized)
