"""持久化 session 事件的版本化编码。"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from pydantic import BaseModel

from cade.agent.events import (
    AssistantEvent,
    AssistantEventBlock,
    AssistantTextBlock,
    ContextWindowResetEvent,
    MessageStartEvent,
    ReasoningDeltaEvent,
    ResponseMetricsEvent,
    TextDeltaEvent,
    ToolResultEvent,
    ToolUpdateEvent,
    ToolUseEvent,
    TurnEndEvent,
    UsageUpdateEvent,
)
from cade.harness.agent_runtime.events import (
    AgentHarnessEvent,
)
from cade.harness.session.surface import encode_surface_messages

from .schema import SESSION_EVENT_SCHEMA_VERSION


def encode_session_event(event: AgentHarnessEvent) -> dict[str, Any]:
    """将运行时事件编码为稳定的持久化 envelope。"""
    return {
        "schema_version": SESSION_EVENT_SCHEMA_VERSION,
        "type": event.type,
        "step": event.step,
        "data": _event_payload(event),
        "correlation": asdict(event.correlation),
    }


def _event_payload(event: AgentHarnessEvent) -> object:
    if isinstance(event, (TextDeltaEvent, ReasoningDeltaEvent)):
        return event.data
    if isinstance(event, MessageStartEvent):
        if isinstance(event.data, BaseModel):
            return event.data.model_dump()
        return None
    if isinstance(event, TurnEndEvent):
        return {
            "tool_results": [
                {"tool_call_id": result.tool_call_id, "content": result.content}
                for result in event.data.tool_results
            ]
        }
    if isinstance(event, AssistantEvent):
        return [_assistant_block_payload(block) for block in event.data]
    if isinstance(event, ToolUseEvent):
        return {
            "id": event.data.id,
            "name": event.data.name,
            "input": event.data.input,
        }
    if isinstance(event, ToolUpdateEvent):
        return {
            "tool_call_id": event.data.tool_call_id,
            "tool_name": event.data.tool_name,
            "partial_result": event.data.partial_result,
        }
    if isinstance(event, ToolResultEvent):
        return {
            "tool_use_id": event.data.tool_use_id,
            "content": event.data.content,
            "status": event.data.status,
            "permission_notice": event.data.permission_notice,
            "approval_denied": event.data.approval_denied,
            "exit_code": event.data.exit_code,
            "metadata": event.data.metadata,
            "render_intent": (
                event.data.render_intent.model_dump()
                if event.data.render_intent is not None
                else None
            ),
            "type": "tool_result",
        }
    if isinstance(event, ContextWindowResetEvent):
        return {
            "window_id": event.data.window_id,
            "messages_removed": event.data.messages_removed,
            "messages_after": event.data.messages_after,
            "trigger": event.data.trigger,
            "replacement": encode_surface_messages(list(event.data.replacement)),
        }
    if isinstance(event, UsageUpdateEvent):
        return dict(event.data)
    if isinstance(event, ResponseMetricsEvent):
        return asdict(event.data)
    return {
        "answer": event.data.answer,
        "steps": event.data.steps,
        "tool_call_count": len(event.data.tool_calls),
        "termination_reason": event.data.termination_reason.value,
        "metrics": event.data.metrics,
        "watchdog_reason": event.data.watchdog_reason,
        "error_detail": event.data.error_detail,
        "needs_follow_up": event.data.needs_follow_up,
        "last_agent": event.data.last_agent,
        "run_state": dict(event.data.run_state),
    }


def _assistant_block_payload(block: AssistantEventBlock) -> dict[str, object]:
    if isinstance(block, AssistantTextBlock):
        return {"type": "text", "text": block.text}
    return {
        "type": "tool_use",
        "id": block.id,
        "name": block.name,
        "input": block.input,
    }
