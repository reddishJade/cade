"""持久化 session 事件的版本化编码。"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from pydantic import BaseModel

from cade.harness.agent_runtime.events import (
    AgentHarnessEvent,
    AssistantEventBlock,
    AssistantStructuredEvent,
    AssistantTextBlock,
    ContextStructuredEvent,
    ContextWindowResetStructuredEvent,
    MessageStartStructuredEvent,
    ReasoningDeltaStructuredEvent,
    ResponseMetricsStructuredEvent,
    TextDeltaStructuredEvent,
    ToolResultStructuredEvent,
    ToolUpdateStructuredEvent,
    ToolUseStructuredEvent,
    TurnEndStructuredEvent,
    UsageUpdateStructuredEvent,
)
from cade.harness.session.surface import encode_surface_messages
from cade.harness.session.window_projection import encode_rollover_spec

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
    if isinstance(event, (TextDeltaStructuredEvent, ReasoningDeltaStructuredEvent)):
        return event.data
    if isinstance(event, ContextStructuredEvent):
        return event.data.model_dump(mode="json")
    if isinstance(event, MessageStartStructuredEvent):
        if isinstance(event.data, BaseModel):
            return event.data.model_dump()
        return None
    if isinstance(event, TurnEndStructuredEvent):
        return {
            "tool_results": [
                {"tool_call_id": result.tool_call_id, "content": result.content}
                for result in event.data.tool_results
            ]
        }
    if isinstance(event, AssistantStructuredEvent):
        blocks = [_assistant_block_payload(block) for block in event.data]
        if event.replay_payload is None and event.reasoning_content is None:
            return blocks
        return {
            "blocks": blocks,
            "reasoning_content": event.reasoning_content,
            "replay_payload": (
                {
                    "protocol": event.replay_payload.protocol,
                    "transport": event.replay_payload.transport,
                    "model": event.replay_payload.model,
                    "output_items": [
                        dict(item) for item in event.replay_payload.output_items
                    ],
                    "reasoning_tokens": event.replay_payload.reasoning_tokens,
                    "output_tokens": event.replay_payload.output_tokens,
                    "output_token_limit": event.replay_payload.output_token_limit,
                }
                if event.replay_payload is not None
                else None
            ),
        }
    if isinstance(event, ToolUseStructuredEvent):
        return {
            "id": event.data.id,
            "name": event.data.name,
            "input": event.data.input,
        }
    if isinstance(event, ToolUpdateStructuredEvent):
        return {
            "tool_call_id": event.data.tool_call_id,
            "tool_name": event.data.tool_name,
            "partial_result": event.data.partial_result,
        }
    if isinstance(event, ToolResultStructuredEvent):
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
    if isinstance(event, ContextWindowResetStructuredEvent):
        data: dict[str, Any] = {
            "window_id": event.data.window_id,
            "messages_removed": event.data.messages_removed,
            "messages_after": event.data.messages_after,
            "trigger": event.data.trigger,
        }
        if event.data.reproject_from_facts:
            data["rollover_spec"] = encode_rollover_spec(
                event.data.rollover_policy,
                preserve_user_request=event.data.preserve_user_request,
            )
        else:
            # 外部自定义换窗函数没有可重放的策略契约，保留快照。
            data["replacement"] = encode_surface_messages(list(event.data.replacement))
        return data
    if isinstance(event, UsageUpdateStructuredEvent):
        return dict(event.data)
    if isinstance(event, ResponseMetricsStructuredEvent):
        return asdict(event.data)
    run_state = (
        event.data.run_state.to_dict() if event.data.run_state is not None else None
    )
    if run_state is not None:
        run_state.pop("messages", None)
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
        "run_state": run_state,
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
