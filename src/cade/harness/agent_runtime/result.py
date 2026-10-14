"""AgentHarness 结果类型与转换。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from cade.agent.events import EventCorrelation
from cade.agent.types import TextContent, ToolCallContent
from cade.ai.events import ProviderFailure, ToolCall

from ...agent.messages import AssistantMessage
from ...agent.results import AgentLoopResult, TerminationReason
from .agent_helpers import text_from_blocks, to_dict
from .events import FinalEvent


@dataclass(frozen=True)
class AgentHarnessResult:
    answer: str
    messages: list[dict[str, Any]]
    steps: int
    tool_calls: list[ToolCall]
    termination_reason: TerminationReason = TerminationReason.COMPLETED
    metrics: dict[str, Any] | None = None
    watchdog_reason: str | None = None
    error_detail: str | None = None
    provider_failure: ProviderFailure | None = None
    needs_follow_up: bool = False
    last_agent: str = "main"
    # 消息历史由会话事件重建；这里只保存产品恢复所需的元数据。
    run_state: dict[str, Any] = field(default_factory=dict)


def build_structured_result(result: AgentLoopResult) -> AgentHarnessResult:
    """将 AgentLoopResult 转换为 AgentHarnessResult。"""
    answer = ""
    tool_calls: list[ToolCall] = []
    messages: list[dict[str, Any]] = []
    for msg in result.messages:
        messages.append(to_dict(msg))
        if not isinstance(msg, AssistantMessage):
            continue
        answer = text_from_blocks(
            [
                {"type": "text", "text": b.text} if isinstance(b, TextContent) else {}
                for b in msg.content
            ]
        )
        for block in msg.content:
            if isinstance(block, ToolCallContent):
                tool_calls.append(
                    ToolCall(
                        id=block.id,
                        name=block.name,
                        input=block.arguments or {},
                    )
                )

    metrics = None
    if result.metrics:
        metrics = {
            "llm_calls": result.metrics.llm_calls,
            "tool_calls": result.metrics.tool_calls,
            "estimated_prompt_tokens": result.metrics.input_tokens,
            "estimated_completion_tokens": result.metrics.output_tokens,
            "model_latencies_ms": result.metrics.model_latencies_ms,
            "tool_latencies_ms": result.metrics.tool_latencies_ms,
            "model_time_ms": sum(result.metrics.model_latencies_ms),
            "tool_time_ms": sum(result.metrics.tool_latencies_ms),
            "steps": result.metrics.steps,
            "context_window_resets": result.metrics.context_window_resets,
        }

    if (
        result.termination_reason is TerminationReason.WATCHDOG
        and result.watchdog_reason
    ):
        if answer:
            answer = answer + " " + result.watchdog_reason
        else:
            answer = result.watchdog_reason
    elif result.termination_reason is TerminationReason.CONTEXT_ERROR:
        answer = result.error_detail or "Cannot assemble context"
    elif (
        result.termination_reason
        in {
            TerminationReason.STEP_LIMIT,
            TerminationReason.LLM_CALL_LIMIT,
        }
        and not answer
    ):
        answer = (
            "step limit reached"
            if result.termination_reason is TerminationReason.STEP_LIMIT
            else "LLM call limit reached"
        )

    return AgentHarnessResult(
        answer=answer,
        messages=messages,
        steps=result.steps,
        tool_calls=tool_calls,
        last_agent="main",
        termination_reason=result.termination_reason,
        metrics=metrics,
        watchdog_reason=result.watchdog_reason,
        error_detail=result.error_detail,
        provider_failure=result.provider_failure,
    )


def final_event(
    step: int,
    result: AgentHarnessResult,
    correlation: EventCorrelation | None = None,
) -> FinalEvent:
    return FinalEvent(
        "final",
        step,
        result,
        correlation or EventCorrelation(),
    )
