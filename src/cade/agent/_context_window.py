"""上下文窗口 token 估算与换窗触发判断。"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence

import tiktoken

from cade.agent.messages import (
    AgentMessage,
    SystemMessage,
)


def estimate_tokens(text: str) -> int:
    try:
        encoding = tiktoken.get_encoding("cl100k_base")
        # 消息正文中的特殊标记是普通文本，不应触发异常并退回字节估算。
        return max(1, len(encoding.encode_ordinary(text)))
    except (KeyError, OSError, RuntimeError, TypeError, UnicodeError, ValueError):
        return max(1, len(text.encode("utf-8")) // 3)


def estimate_message_tokens(messages: Sequence[AgentMessage]) -> int:
    """所有历史和工作集预算复用实际请求消息计量。"""
    from ._codec import convert_to_llm

    return sum(estimate_wire_message_tokens(m) for m in convert_to_llm(list(messages)))


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
    replay = message.get("_cade_replay_payload")
    if isinstance(replay, Mapping) and replay.get("protocol") == "openai_responses":
        reasoning_tokens = replay.get("reasoning_tokens")
        output_tokens = replay.get("output_tokens")
        if (
            isinstance(reasoning_tokens, int)
            and not isinstance(reasoning_tokens, bool)
            and reasoning_tokens >= 0
        ):
            replay_reasoning_tokens = reasoning_tokens
        else:
            replay_reasoning_tokens = None
        output_token_limit = replay.get("output_token_limit")
        if (
            isinstance(output_tokens, int)
            and not isinstance(output_tokens, bool)
            and output_tokens >= 0
        ):
            replay_output_budget = output_tokens
        elif (
            isinstance(output_token_limit, int)
            and not isinstance(output_token_limit, bool)
            and output_token_limit >= 0
        ):
            replay_output_budget = output_token_limit
        else:
            replay_output_budget = None
        raw_items = replay.get("output_items")
        has_reasoning_item = isinstance(raw_items, list) and any(
            isinstance(item, Mapping) and item.get("type") == "reasoning"
            for item in raw_items
        )
        if (
            replay_reasoning_tokens is None
            and has_reasoning_item
            and replay_output_budget is not None
        ):
            return 4 + replay_output_budget

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
    if isinstance(replay, Mapping):
        reasoning_tokens = replay.get("reasoning_tokens")
        if (
            isinstance(reasoning_tokens, int)
            and not isinstance(reasoning_tokens, bool)
            and reasoning_tokens >= 0
        ):
            total += reasoning_tokens
        output_tokens = replay.get("output_tokens")
        if (
            isinstance(output_tokens, int)
            and not isinstance(output_tokens, bool)
            and output_tokens >= 0
        ):
            total = max(total, 4 + output_tokens)
    return total
