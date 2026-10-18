"""AgentEvent 到 AgentHarnessEvent 的事件翻译。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from cade.agent.context_policy import ContextPolicy
from cade.agent.types import (
    TextContent,
    ToolArguments,
    ToolCallContent,
    ToolRenderIntent,
)
from cade.ai.events import ProviderReplayPayload, ToolCall

from ...agent.events import (
    AgentEvent,
    ContextWindowResetEvent,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ResponseMetricsEvent,
    ThinkingUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    TurnEndEvent,
    TurnStartEvent,
    UsageUpdateEvent,
)
from ...agent.messages import AgentMessage, AssistantMessage, ContextMessage
from ...agent.types import AgentToolResult
from ..observability import EventCorrelation, RuntimeCorrelation

if TYPE_CHECKING:
    from .result import AgentHarnessResult


@dataclass(frozen=True)
class TurnToolResultData:
    tool_call_id: str
    content: str


@dataclass(frozen=True)
class TurnEndData:
    tool_results: list[TurnToolResultData]


@dataclass(frozen=True)
class TextDeltaStructuredEvent:
    type: Literal["text_delta"]
    step: int
    data: str
    correlation: EventCorrelation = field(default_factory=EventCorrelation)


@dataclass(frozen=True)
class ReasoningDeltaStructuredEvent:
    type: Literal["reasoning_delta"]
    step: int
    data: str
    correlation: EventCorrelation = field(default_factory=EventCorrelation)


@dataclass(frozen=True)
class UsageUpdateStructuredEvent:
    type: Literal["usage_update"]
    step: int
    data: dict[str, int]
    correlation: EventCorrelation = field(default_factory=EventCorrelation)


@dataclass(frozen=True)
class ResponseMetricsData:
    """请求级性能；TTFT 至首文本或思考增量，TPS 使用服务端输出用量。"""

    ttft_seconds: float | None
    tokens_per_second: float | None
    completed: bool


@dataclass(frozen=True)
class ResponseMetricsStructuredEvent:
    type: Literal["response_metrics"]
    step: int
    data: ResponseMetricsData
    correlation: EventCorrelation = field(default_factory=EventCorrelation)


@dataclass(frozen=True)
class MessageStartStructuredEvent:
    type: Literal["message_start"]
    step: int
    data: AgentMessage | None
    correlation: EventCorrelation = field(default_factory=EventCorrelation)


@dataclass(frozen=True)
class TurnEndStructuredEvent:
    type: Literal["turn_end"]
    step: int
    data: TurnEndData
    correlation: EventCorrelation = field(default_factory=EventCorrelation)


@dataclass(frozen=True)
class AssistantTextBlock:
    text: str


@dataclass(frozen=True)
class AssistantToolUseBlock:
    id: str
    name: str
    input: ToolArguments


type AssistantEventBlock = AssistantTextBlock | AssistantToolUseBlock


@dataclass(frozen=True)
class AssistantStructuredEvent:
    type: Literal["assistant"]
    step: int
    data: tuple[AssistantEventBlock, ...]
    correlation: EventCorrelation = field(default_factory=EventCorrelation)
    replay_payload: ProviderReplayPayload | None = None
    reasoning_content: str | None = None


@dataclass(frozen=True)
class ContextStructuredEvent:
    type: Literal["runtime_context"]
    step: int
    data: ContextMessage
    correlation: EventCorrelation = field(default_factory=EventCorrelation)


@dataclass(frozen=True)
class ToolUseStructuredEvent:
    type: Literal["tool_use"]
    step: int
    data: ToolCall
    correlation: EventCorrelation = field(default_factory=EventCorrelation)


@dataclass(frozen=True)
class ToolUpdateData:
    tool_call_id: str
    tool_name: str
    partial_result: str


@dataclass(frozen=True)
class ToolUpdateStructuredEvent:
    type: Literal["tool_update"]
    step: int
    data: ToolUpdateData
    correlation: EventCorrelation = field(default_factory=EventCorrelation)


@dataclass(frozen=True)
class ToolResultBlock:
    tool_use_id: str
    content: str
    status: Literal["ok", "error"] = "ok"
    permission_notice: str | None = None
    approval_denied: bool = False
    exit_code: int | None = None
    render_intent: ToolRenderIntent | None = None
    type: str = "tool_result"
    metadata: dict[str, object] | None = None


@dataclass(frozen=True)
class ToolResultStructuredEvent:
    type: Literal["tool_result"]
    step: int
    data: ToolResultBlock
    correlation: EventCorrelation = field(default_factory=EventCorrelation)


@dataclass(frozen=True)
class ContextWindowResetData:
    window_id: str
    messages_removed: int
    messages_after: int
    trigger: str
    replacement: tuple[AgentMessage, ...]
    reproject_from_facts: bool = False
    rollover_policy: ContextPolicy | None = None
    preserve_user_request: bool = True


@dataclass(frozen=True)
class ContextWindowResetStructuredEvent:
    type: Literal["context_window_reset"]
    step: int
    data: ContextWindowResetData
    correlation: EventCorrelation = field(default_factory=EventCorrelation)


@dataclass(frozen=True)
class FinalStructuredEvent:
    type: Literal["final"]
    step: int
    data: AgentHarnessResult
    correlation: EventCorrelation = field(default_factory=EventCorrelation)


type AgentHarnessEvent = (
    MessageStartStructuredEvent
    | TurnEndStructuredEvent
    | TextDeltaStructuredEvent
    | ReasoningDeltaStructuredEvent
    | UsageUpdateStructuredEvent
    | ResponseMetricsStructuredEvent
    | AssistantStructuredEvent
    | ContextStructuredEvent
    | ToolUseStructuredEvent
    | ToolUpdateStructuredEvent
    | ToolResultStructuredEvent
    | ContextWindowResetStructuredEvent
    | FinalStructuredEvent
)


def is_model_fact_event(event: AgentHarnessEvent) -> bool:
    """判断事件是否改变可重放的模型历史。"""
    return isinstance(
        event,
        (
            AssistantStructuredEvent,
            ContextStructuredEvent,
            ToolUseStructuredEvent,
            ToolResultStructuredEvent,
            ContextWindowResetStructuredEvent,
        ),
    )


@dataclass
class _StreamTranslationState:
    step: int = 0
    text_seen: dict[int, str] = field(default_factory=dict)
    correlation: RuntimeCorrelation = field(
        default_factory=lambda: RuntimeCorrelation("local")
    )


def translate_event(
    event: AgentEvent,
    state: _StreamTranslationState,
) -> AgentHarnessEvent | list[AgentHarnessEvent] | None:
    if isinstance(event, TurnStartEvent):
        state.step += 1
        return None

    if isinstance(event, MessageUpdateEvent):
        return _translate_message_update(event, state)

    if isinstance(event, MessageStartEvent):
        return _translate_message_start(event, state)

    if isinstance(event, TurnEndEvent):
        return _translate_turn_end(event, state)

    if isinstance(event, ThinkingUpdateEvent):
        return _translate_thinking_update(event, state)

    if isinstance(event, UsageUpdateEvent):
        return UsageUpdateStructuredEvent(
            "usage_update",
            state.step,
            {
                "input_tokens": event.input_tokens,
                "output_tokens": event.output_tokens,
            },
            state.correlation.snapshot(),
        )

    if isinstance(event, ResponseMetricsEvent):
        if not event.completed:
            # 同一 step 的重试或续写也属于新消息，文本差分不能沿用上次请求。
            state.text_seen.pop(state.step, None)
        return ResponseMetricsStructuredEvent(
            "response_metrics",
            state.step,
            ResponseMetricsData(
                event.ttft_seconds, event.tokens_per_second, event.completed
            ),
            state.correlation.snapshot(),
        )

    if isinstance(event, MessageEndEvent):
        return _translate_message_end(event, state)

    if isinstance(event, ToolExecutionStartEvent):
        return _translate_tool_execution_start(event, state)

    if isinstance(event, ToolExecutionUpdateEvent):
        return _translate_tool_execution_update(event, state)

    if isinstance(event, ToolExecutionEndEvent):
        return _translate_tool_execution_end(event, state)

    return _translate_context_window_reset(event, state)


def _translate_message_update(
    event: MessageUpdateEvent,
    state: _StreamTranslationState,
) -> AgentHarnessEvent | None:
    msg = event.message
    if not isinstance(msg, AssistantMessage) or not msg.content:
        return None
    for block in msg.content:
        if isinstance(block, TextContent) and block.text:
            step = state.step
            prev = state.text_seen.get(step, "")
            full = block.text
            delta = full[len(prev) :]
            if not delta:
                return None
            state.text_seen[step] = full
            return TextDeltaStructuredEvent(
                "text_delta",
                step,
                delta,
                state.correlation.snapshot(),
            )
    return None


def _translate_message_start(
    event: MessageStartEvent,
    state: _StreamTranslationState,
) -> MessageStartStructuredEvent | None:
    if isinstance(event.message, ContextMessage):
        return None
    return MessageStartStructuredEvent(
        "message_start",
        state.step,
        event.message,
        state.correlation.snapshot(),
    )


def _translate_turn_end(
    event: TurnEndEvent,
    state: _StreamTranslationState,
) -> TurnEndStructuredEvent:
    return TurnEndStructuredEvent(
        "turn_end",
        state.step,
        TurnEndData(
            tool_results=[
                TurnToolResultData(r.tool_call_id, str(r.content))
                for r in event.tool_results
            ]
        ),
        state.correlation.snapshot(),
    )


def _translate_thinking_update(
    event: ThinkingUpdateEvent,
    state: _StreamTranslationState,
) -> ReasoningDeltaStructuredEvent:
    return ReasoningDeltaStructuredEvent(
        "reasoning_delta",
        state.step,
        event.reasoning_content,
        state.correlation.snapshot(),
    )


def _translate_message_end(
    event: MessageEndEvent,
    state: _StreamTranslationState,
) -> AssistantStructuredEvent | ContextStructuredEvent | None:
    msg = event.message
    if isinstance(msg, ContextMessage):
        return ContextStructuredEvent(
            "runtime_context", state.step, msg, state.correlation.snapshot()
        )
    if not isinstance(msg, AssistantMessage) or (
        not msg.content and msg.replay_payload is None
    ):
        return None
    blocks = _assistant_event_blocks(msg)
    if not blocks and msg.replay_payload is None:
        return None
    return AssistantStructuredEvent(
        "assistant",
        state.step,
        blocks,
        state.correlation.snapshot(),
        msg.replay_payload,
        msg.reasoning_content,
    )


def _translate_tool_execution_start(
    event: ToolExecutionStartEvent,
    state: _StreamTranslationState,
) -> ToolUseStructuredEvent:
    tool_use = ToolCall(
        id=event.tool_call_id,
        name=event.tool_name,
        input=event.args or {},
    )
    return ToolUseStructuredEvent(
        "tool_use",
        state.step,
        tool_use,
        state.correlation.snapshot(event.tool_call_id),
    )


def _translate_tool_execution_update(
    event: ToolExecutionUpdateEvent,
    state: _StreamTranslationState,
) -> ToolUpdateStructuredEvent:
    return ToolUpdateStructuredEvent(
        "tool_update",
        state.step,
        ToolUpdateData(
            tool_call_id=event.tool_call_id,
            tool_name=event.tool_name,
            partial_result=_tool_update_text(event.partial_result),
        ),
        state.correlation.snapshot(event.tool_call_id),
    )


def _translate_tool_execution_end(
    event: ToolExecutionEndEvent,
    state: _StreamTranslationState,
) -> AgentHarnessEvent | list[AgentHarnessEvent]:
    permission_notice: str | None = None
    approval_denied = False
    exit_code: int | None = None
    if event.result is not None and event.result.metadata is not None:
        raw_notice = event.result.metadata.get("permission_notice")
        if isinstance(raw_notice, str):
            permission_notice = raw_notice
        approval_denied = event.result.metadata.get("approval_denied") is True
        raw_exit_code = event.result.metadata.get("exit_code")
        if isinstance(raw_exit_code, int) and not isinstance(raw_exit_code, bool):
            exit_code = raw_exit_code
    result_event = ToolResultStructuredEvent(
        "tool_result",
        state.step,
        ToolResultBlock(
            tool_use_id=event.tool_call_id,
            content=str(event.result.content) if event.result else "",
            status="error" if event.is_error else "ok",
            permission_notice=permission_notice,
            approval_denied=approval_denied,
            exit_code=exit_code,
            render_intent=(event.result.render_intent if event.result else None),
            metadata=(event.result.metadata if event.result else None),
        ),
        state.correlation.snapshot(event.tool_call_id),
    )
    return result_event


def _translate_context_window_reset(
    event: ContextWindowResetEvent,
    state: _StreamTranslationState,
) -> ContextWindowResetStructuredEvent:
    return ContextWindowResetStructuredEvent(
        "context_window_reset",
        state.step,
        ContextWindowResetData(
            window_id=event.window_id,
            messages_removed=event.messages_removed,
            messages_after=event.messages_after,
            trigger=event.trigger,
            replacement=tuple(event.replacement),
            reproject_from_facts=event.reproject_from_facts,
            rollover_policy=event.rollover_policy,
            preserve_user_request=event.preserve_user_request,
        ),
        state.correlation.snapshot(),
    )


def _tool_update_text(partial_result: AgentToolResult | None) -> str:
    if partial_result is None:
        return ""
    parts: list[str] = []
    for block in partial_result.content:
        if isinstance(block, TextContent):
            parts.append(block.text)
        elif isinstance(block, ToolCallContent):
            pass
        else:
            parts.append(str(block))
    return "".join(part for part in parts if part)


def _assistant_event_blocks(msg: AssistantMessage) -> tuple[AssistantEventBlock, ...]:
    blocks: list[AssistantEventBlock] = []
    for block in msg.content:
        if isinstance(block, TextContent):
            blocks.append(AssistantTextBlock(block.text))
        elif isinstance(block, ToolCallContent):
            blocks.append(
                AssistantToolUseBlock(block.id, block.name, block.arguments or {})
            )
    return tuple(blocks)
