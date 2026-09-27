"""上下文窗口 token 估算与换窗触发判断。"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence

import tiktoken

from cade.agent.messages import (
    AgentMessage,
    AssistantMessage,
    BranchSummaryMessage,
    SystemMessage,
)
from cade.agent.types import TextContent, ThinkingContent, ToolCallContent

_ENCODING_CACHE: dict[str, tiktoken.Encoding] = {}
_DEFAULT_ENCODING = "cl100k_base"


def _get_encoding(name: str = _DEFAULT_ENCODING) -> tiktoken.Encoding:
    if name not in _ENCODING_CACHE:
        _ENCODING_CACHE[name] = tiktoken.get_encoding(name)
    return _ENCODING_CACHE[name]


def estimate_tokens(text: str) -> int:
    try:
        encoding = _get_encoding()
        return max(1, len(encoding.encode(text)))
    except (KeyError, RuntimeError, TypeError, UnicodeError, ValueError):
        return max(1, len(text.encode("utf-8")) // 3)


def estimate_message_tokens(messages: Sequence[AgentMessage]) -> int:
    total = 0
    for message in messages:
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextContent):
                    total += estimate_tokens(block.text)
                elif isinstance(block, ThinkingContent):
                    total += estimate_tokens(block.thinking)
                elif isinstance(block, ToolCallContent):
                    total += estimate_tokens(
                        json.dumps(block.arguments or {}, default=str)
                    )
        elif isinstance(message, BranchSummaryMessage):
            total += estimate_tokens(message.summary)
        else:
            content = message.content
            if isinstance(content, str):
                total += estimate_tokens(content)
            else:
                for block in content:
                    if isinstance(block, TextContent):
                        total += estimate_tokens(block.text)
    return total


def should_rollover_token_aware(
    messages: Sequence[AgentMessage],
    *,
    last_prompt_tokens: int | None = None,
    fallback_threshold: int = 32_000,
    message_threshold: int = 0,
    token_threshold: int = 0,
) -> bool:
    """优先使用 provider 用量，静态估算只作为兜底。"""
    if last_prompt_tokens is not None:
        return last_prompt_tokens >= fallback_threshold
    if message_threshold > 0 and len(messages) >= message_threshold:
        return True
    if token_threshold > 0:
        return estimate_message_tokens(messages) >= token_threshold
    return False


def extract_prompt_tokens_from_usage(usage: Mapping[str, object] | None) -> int | None:
    if not usage:
        return None
    prompt_tokens = usage.get("prompt_tokens")
    if isinstance(prompt_tokens, int) and not isinstance(prompt_tokens, bool):
        return prompt_tokens
    return None


_WINDOW_ID_PATTERN = re.compile(r'<context-window-reset id="([^"]+)">')


def context_window_id(messages: Sequence[AgentMessage]) -> str:
    """读取持久化换窗协议中的 ID，使重启后的诊断仍能关联同一窗口。"""
    for message in reversed(messages):
        if not isinstance(message, SystemMessage):
            continue
        match = _WINDOW_ID_PATTERN.search(message.content)
        if match is not None:
            return match.group(1)
    return ""


def estimate_wire_message_tokens(
    message: Mapping[str, object],
    count_tokens: Callable[[str], int] = estimate_tokens,
) -> int:
    """按实际消息正文估算，避免把 JSON 转义与 Python repr 算成模型输入。"""
    total = 4
    content = message.get("content")
    if isinstance(content, str):
        total += count_tokens(content)
    elif isinstance(content, list):
        for block in content:
            if (
                isinstance(block, dict)
                and block.get("type") == "text"
                and isinstance(block.get("text"), str)
            ):
                total += count_tokens(block["text"])
            else:
                total += count_tokens(
                    json.dumps(block, ensure_ascii=False, default=str)
                )
    elif content:
        total += count_tokens(json.dumps(content, ensure_ascii=False, default=str))
    reasoning = message.get("reasoning_content")
    if isinstance(reasoning, str):
        total += count_tokens(reasoning)
    calls = message.get("tool_calls")
    if isinstance(calls, list):
        for call in calls:
            if not isinstance(call, dict):
                continue
            function = call.get("function", {})
            if isinstance(function, dict):
                for key in ("name", "arguments"):
                    value = function.get(key)
                    if isinstance(value, str):
                        total += count_tokens(value)
            total += 4
    return total
