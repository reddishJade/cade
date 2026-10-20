"""流式事件解码。

处理 Chat Completions 流式响应，
将原始 chunk 转换为统一的 ProviderEvent。
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Iterator, Sequence
from typing import Protocol

import orjson

from cade.ai.cache import extract_cache_usage
from cade.ai.events import (
    FinalMessage,
    ReasoningDelta,
    TextDelta,
    ToolCall,
    ToolCallEvent,
    UsageUpdate,
)
from cade.ai.types import ToolArguments

# ── OpenAI chunk 结构 Protocol ──


class _Usage(Protocol):
    @property
    def prompt_tokens(self) -> int: ...
    @property
    def completion_tokens(self) -> int: ...


class _ChoiceDeltaToolCallFunction(Protocol):
    @property
    def name(self) -> str | None: ...
    @property
    def arguments(self) -> str | None: ...


class _ChoiceDeltaToolCall(Protocol):
    @property
    def index(self) -> int: ...
    @property
    def id(self) -> str | None: ...
    @property
    def function(self) -> _ChoiceDeltaToolCallFunction | None: ...


class _ChoiceDelta(Protocol):
    @property
    def content(self) -> str | None: ...
    @property
    def tool_calls(self) -> Sequence[_ChoiceDeltaToolCall] | None: ...


class _Choice(Protocol):
    @property
    def delta(self) -> _ChoiceDelta: ...


class _ChatCompletionChunk(Protocol):
    @property
    def usage(self) -> _Usage | None: ...
    @property
    def choices(self) -> Sequence[_Choice]: ...


# ── 工具调用解析 ──


def parse_tool_arguments(raw_arguments: str) -> ToolArguments:
    try:
        result = orjson.loads((raw_arguments or "{}").encode())
        parsed = _tool_arguments(result)
        if parsed is not None:
            return parsed
        return {"__invalid_tool_arguments__": str(result)}
    except orjson.JSONDecodeError:
        return {"__invalid_tool_arguments__": raw_arguments}


def _tool_arguments(value: object) -> ToolArguments | None:
    if not isinstance(value, dict):
        return None
    return {str(key): item for key, item in value.items()}


# ── 流式事件解码 ──


def chat_stream_to_events(
    stream: Iterable[_ChatCompletionChunk],
) -> Iterator[TextDelta | ReasoningDelta | ToolCallEvent | UsageUpdate | FinalMessage]:
    """将 Chat Completions 流式 chunk 转换为统一事件流。"""
    calls: dict[int, dict[str, str]] = defaultdict(
        lambda: {"id": "", "name": "", "arguments": ""}
    )
    finish_reason: str | None = None
    text_parts: list[str] = []
    for chunk in stream:
        usage = getattr(chunk, "usage", None)
        if usage is not None:
            yield UsageUpdate(
                input_tokens=usage.prompt_tokens or 0,
                output_tokens=usage.completion_tokens or 0,
                cached_input_tokens=extract_cache_usage(chunk).hit_tokens,
            )

        choices = chunk.choices
        if not choices:
            continue
        finish_reason = getattr(choices[0], "finish_reason", None) or finish_reason
        delta = choices[0].delta
        reasoning = getattr(delta, "reasoning_content", None)
        if reasoning:
            yield ReasoningDelta(chunk=str(reasoning))
        text = delta.content
        if text:
            text_parts.append(str(text))
            yield TextDelta(chunk=str(text))
        for call in delta.tool_calls or []:
            index = call.index
            current = calls[index]
            if call.id is not None:
                current["id"] = call.id
            func = call.function
            if func is not None:
                if func.name is not None:
                    current["name"] = func.name
                if func.arguments is not None:
                    current["arguments"] += func.arguments

    ready = [
        ToolCall(
            id=c["id"],
            name=c["name"],
            input=parse_tool_arguments(c["arguments"]),
        )
        for _, c in sorted(calls.items())
    ]
    if ready:
        yield ToolCallEvent(calls=ready)
    if finish_reason is not None:
        yield FinalMessage(
            content="".join(text_parts),
            stop_reason=(
                "max_tokens"
                if finish_reason == "length"
                else "tool_use"
                if ready and finish_reason in {"tool_calls", "stop"}
                else "end_turn"
                if finish_reason == "stop"
                else "error"
            ),
        )
