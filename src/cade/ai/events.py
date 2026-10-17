"""Provider 流式事件协议。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from cade.ai.types import ToolArguments

type Message = dict[str, object]
type ProviderJsonValue = (
    str
    | int
    | float
    | bool
    | None
    | list["ProviderJsonValue"]
    | dict[str, "ProviderJsonValue"]
)
type StopReason = Literal[
    "end_turn",
    "max_tokens",
    "tool_use",
    "max_steps",
    "cancelled",
    "error",
    "stop_sequence",
    "aborted",
]


@dataclass(frozen=True)
class ToolCall:
    """单次工具调用。"""

    id: str
    name: str
    input: ToolArguments


@dataclass(frozen=True)
class TextDelta:
    """文本增量。"""

    chunk: str


@dataclass(frozen=True)
class ToolCallEvent:
    """一组工具调用。"""

    calls: list[ToolCall]


@dataclass(frozen=True)
class UsageUpdate:
    """token 用量更新。"""

    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class FinalMessage:
    """流式响应终止消息。"""

    content: str
    stop_reason: StopReason
    replay_payload: ProviderReplayPayload | None = None


@dataclass(frozen=True)
class ProviderReplayPayload:
    """完整响应项的 provider 专属重放数据。"""

    protocol: Literal["openai_responses"]
    transport: str
    model: str
    output_items: tuple[dict[str, ProviderJsonValue], ...]
    reasoning_tokens: int | None = None
    output_tokens: int | None = None
    output_token_limit: int | None = None


@dataclass(frozen=True)
class ProviderFailure:
    """provider 流失败的可序列化分类信息。"""

    message: str
    exception_type: str
    status_code: int | None = None


@dataclass(frozen=True)
class ReasoningDelta:
    """推理内容增量。"""

    chunk: str


type ProviderEvent = (
    TextDelta
    | ToolCallEvent
    | UsageUpdate
    | FinalMessage
    | ProviderFailure
    | ReasoningDelta
)
