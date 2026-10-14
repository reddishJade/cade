"""循环、会话记录与宿主共享的运行事件。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from cade.ai.events import ToolCall

from .messages import (
    AgentMessage,
    AssistantMessage,
    ToolResultMessage,
    ToolResultMessageContent,
)
from .types import TextContent, ToolArguments, ToolCallContent, ToolRenderIntent


@dataclass(frozen=True)
class EventCorrelation:
    """事件与请求、工具调用关联的不可变快照。"""

    timestamp: str = ""
    session_id: str = ""
    turn_id: str = ""
    request_id: str = ""
    tool_call_id: str = ""


@dataclass(frozen=True, kw_only=True)
class TurnToolResultData:
    tool_call_id: str
    content: str


@dataclass(frozen=True, kw_only=True)
class TurnEndData:
    tool_results: list[TurnToolResultData]


@dataclass(frozen=True, kw_only=True)
class TextDeltaEvent:
    type: Literal["text_delta"] = "text_delta"
    step: int = 0
    data: str
    correlation: EventCorrelation = field(default_factory=EventCorrelation)


@dataclass(frozen=True, kw_only=True)
class ReasoningDeltaEvent:
    type: Literal["reasoning_delta"] = "reasoning_delta"
    step: int = 0
    data: str
    correlation: EventCorrelation = field(default_factory=EventCorrelation)


@dataclass(frozen=True, kw_only=True)
class UsageUpdateEvent:
    type: Literal["usage_update"] = "usage_update"
    step: int = 0
    data: dict[str, int]
    correlation: EventCorrelation = field(default_factory=EventCorrelation)


@dataclass(frozen=True, kw_only=True)
class ResponseMetricsData:
    """请求级性能；TTFT 至首文本或思考增量，TPS 使用服务端输出用量。"""

    ttft_seconds: float | None
    tokens_per_second: float | None
    completed: bool


@dataclass(frozen=True, kw_only=True)
class ResponseMetricsEvent:
    type: Literal["response_metrics"] = "response_metrics"
    step: int = 0
    data: ResponseMetricsData
    correlation: EventCorrelation = field(default_factory=EventCorrelation)


@dataclass(frozen=True, kw_only=True)
class MessageStartEvent:
    type: Literal["message_start"] = "message_start"
    step: int = 0
    data: AgentMessage | None
    correlation: EventCorrelation = field(default_factory=EventCorrelation)


@dataclass(frozen=True, kw_only=True)
class TurnEndEvent:
    type: Literal["turn_end"] = "turn_end"
    step: int = 0
    data: TurnEndData
    correlation: EventCorrelation = field(default_factory=EventCorrelation)


@dataclass(frozen=True, kw_only=True)
class AssistantTextBlock:
    text: str


@dataclass(frozen=True, kw_only=True)
class AssistantToolUseBlock:
    id: str
    name: str
    input: ToolArguments


type AssistantEventBlock = AssistantTextBlock | AssistantToolUseBlock


@dataclass(frozen=True, kw_only=True)
class AssistantEvent:
    type: Literal["assistant"] = "assistant"
    step: int = 0
    data: tuple[AssistantEventBlock, ...]
    correlation: EventCorrelation = field(default_factory=EventCorrelation)


@dataclass(frozen=True, kw_only=True)
class ToolUseEvent:
    type: Literal["tool_use"] = "tool_use"
    step: int = 0
    data: ToolCall
    correlation: EventCorrelation = field(default_factory=EventCorrelation)


@dataclass(frozen=True, kw_only=True)
class ToolUpdateData:
    tool_call_id: str
    tool_name: str
    partial_result: str


@dataclass(frozen=True, kw_only=True)
class ToolUpdateEvent:
    type: Literal["tool_update"] = "tool_update"
    step: int = 0
    data: ToolUpdateData
    correlation: EventCorrelation = field(default_factory=EventCorrelation)


@dataclass(frozen=True, kw_only=True)
class ToolResultBlock:
    tool_use_id: str
    content: ToolResultMessageContent
    status: Literal["ok", "error"] = "ok"
    permission_notice: str | None = None
    approval_denied: bool = False
    exit_code: int | None = None
    render_intent: ToolRenderIntent | None = None
    type: str = "tool_result"
    metadata: dict[str, object] | None = None


@dataclass(frozen=True, kw_only=True)
class ToolResultEvent:
    type: Literal["tool_result"] = "tool_result"
    step: int = 0
    data: ToolResultBlock
    correlation: EventCorrelation = field(default_factory=EventCorrelation)


@dataclass(frozen=True, kw_only=True)
class ContextWindowResetData:
    window_id: str
    messages_removed: int
    messages_after: int
    trigger: str
    replacement: tuple[AgentMessage, ...]


@dataclass(frozen=True, kw_only=True)
class ContextWindowResetEvent:
    type: Literal["context_window_reset"] = "context_window_reset"
    step: int = 0
    data: ContextWindowResetData
    correlation: EventCorrelation = field(default_factory=EventCorrelation)


type AgentEvent = (
    TextDeltaEvent
    | ReasoningDeltaEvent
    | UsageUpdateEvent
    | ResponseMetricsEvent
    | MessageStartEvent
    | TurnEndEvent
    | AssistantEvent
    | ToolUseEvent
    | ToolUpdateEvent
    | ToolResultEvent
    | ContextWindowResetEvent
)


def assistant_event(message: AssistantMessage) -> AssistantEvent | None:
    """只发布宿主和 transcript 所需的文本与工具调用。"""
    blocks: list[AssistantEventBlock] = []
    for block in message.content:
        if isinstance(block, TextContent):
            blocks.append(AssistantTextBlock(text=block.text))
        elif isinstance(block, ToolCallContent):
            blocks.append(
                AssistantToolUseBlock(
                    id=block.id,
                    name=block.name,
                    input=block.arguments or {},
                )
            )
    return AssistantEvent(data=tuple(blocks)) if blocks else None


def tool_result_event(result: ToolResultMessage) -> ToolResultEvent:
    """发布工具结果，并投影宿主读取的权限与进程字段。"""
    metadata = result.metadata or {}
    notice = metadata.get("permission_notice")
    exit_code = metadata.get("exit_code")
    return ToolResultEvent(
        data=ToolResultBlock(
            tool_use_id=result.tool_call_id,
            content=result.content,
            status="error" if result.is_error else "ok",
            permission_notice=notice if isinstance(notice, str) else None,
            approval_denied=metadata.get("approval_denied") is True,
            exit_code=exit_code
            if isinstance(exit_code, int) and not isinstance(exit_code, bool)
            else None,
            render_intent=result.render_intent,
            metadata=result.metadata,
        )
    )
