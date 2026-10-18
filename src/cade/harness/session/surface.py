"""从会话事实重建当前窗口的原始消息来源。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast

from pydantic import ValidationError

from cade.agent._tool_pairing import repair_tool_pairing
from cade.agent.messages import (
    AgentMessage,
    AssistantMessage,
    BranchSummaryMessage,
    ContextMessage,
    SystemMessage,
    ToolResultMessage,
    UserMessage,
)
from cade.agent.types import ToolCallContent
from cade.ai.events import ProviderReplayPayload

from .artifacts import resolve_tool_result_artifact
from .schema import SESSION_EVENT_SCHEMA_VERSION, supports_session_event_schema
from .types import JsonValue, SessionEntry
from .window_projection import apply_rollover_spec


class InvalidSessionSurfaceError(ValueError):
    """持久化 surface replacement 无法形成合法模型历史。"""


@dataclass(frozen=True)
class SessionSurface:
    """由事实日志计算出的当前模型历史。"""

    messages: tuple[AgentMessage, ...]
    generation: int = 0
    replacement_entry_id: str | None = None


_MESSAGE_TYPES = {
    "system": SystemMessage,
    "user": UserMessage,
    "assistant": AssistantMessage,
    "tool_result": ToolResultMessage,
    "branch_summary": BranchSummaryMessage,
    "context": ContextMessage,
}


def encode_surface_messages(messages: list[AgentMessage]) -> list[JsonValue]:
    """以带显式类型标签的格式编码内部消息。"""
    validate_tool_pairing(messages)
    encoded: list[JsonValue] = []
    for message in messages:
        payload = message.model_dump(mode="json")
        encoded.append(
            {
                "kind": _message_kind(message),
                "payload": cast(JsonValue, payload),
            }
        )
    return encoded


def decode_surface_messages(value: object) -> list[AgentMessage]:
    """严格解码 surface replacement，不接受未标记旧格式。"""
    if not isinstance(value, list):
        raise InvalidSessionSurfaceError("surface replacement must be a list")
    messages: list[AgentMessage] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise InvalidSessionSurfaceError(
                f"surface message {index} must be an object"
            )
        kind = item.get("kind")
        payload = item.get("payload")
        model = _MESSAGE_TYPES.get(str(kind))
        if model is None or not isinstance(payload, dict):
            raise InvalidSessionSurfaceError(
                f"surface message {index} has invalid kind or payload"
            )
        try:
            messages.append(model.model_validate(payload))
        except ValueError as exc:
            raise InvalidSessionSurfaceError(
                f"surface message {index} is invalid"
            ) from exc
    validate_tool_pairing(messages)
    return messages


def project_session_surface(
    records: list[SessionEntry], *, artifacts_dir: Path | None = None
) -> SessionSurface:
    """从事件事实重建窗口；兼容历史 replacement 快照。"""
    messages: list[AgentMessage] = []
    pending_tool_calls: list[ToolCallContent] = []
    open_tool_calls: set[str] = set()
    deferred_inputs: list[AgentMessage] = []
    seen_tool_call_ids: set[str] = set()
    event_assistant_texts: list[str] = []
    tool_artifacts: dict[str, object] = {}
    generation = 0
    replacement_entry_id: str | None = None
    current_run_id: str | None = None

    for record_index, record in enumerate(records):
        if record.type == "user":
            raise InvalidSessionSurfaceError(
                "legacy user entries are not part of the session schema"
            )
        if record.type == "assistant":
            _append_plain_assistant(messages, record, event_assistant_texts)
            event_assistant_texts.clear()
            continue
        if record.type != "event" or not isinstance(record.content, dict):
            continue

        event_type = record.content.get("type")
        data = record.content.get("data")
        if event_type == "runtime_context":
            if record.content.get("schema_version") != SESSION_EVENT_SCHEMA_VERSION:
                raise InvalidSessionSurfaceError("unsupported runtime context schema")
            try:
                messages.append(ContextMessage.model_validate(data))
            except ValidationError as exc:
                raise InvalidSessionSurfaceError(
                    "invalid runtime context message"
                ) from exc
            continue
        if event_type == "inbox/claimed":
            if not supports_session_event_schema(record.content.get("schema_version")):
                raise InvalidSessionSurfaceError("unsupported inbox claim event schema")
            if not isinstance(data, dict):
                raise InvalidSessionSurfaceError(
                    "inbox claim event data must be an object"
                )
            claimed = decode_surface_messages(data.get("message"))
            if len(claimed) != 1:
                raise InvalidSessionSurfaceError(
                    "inbox claim event must contain exactly one message"
                )
            claimed_message = claimed[0]
            source = data.get("source")
            if isinstance(claimed_message, UserMessage) and source in {
                "user",
                "runtime",
                "subagent",
            }:
                claimed_message = claimed_message.model_copy(update={"source": source})
            run_id = data.get("run_id")
            if isinstance(run_id, str) and run_id and run_id != current_run_id:
                # 新 run 关闭旧的中断批次；同 run 输入仍等完整工具结果。
                if open_tool_calls:
                    messages = repair_tool_pairing(messages)
                    messages.extend(deferred_inputs)
                    deferred_inputs.clear()
                    pending_tool_calls.clear()
                    open_tool_calls.clear()
                    seen_tool_call_ids = _tool_call_ids(messages)
                current_run_id = run_id
            if open_tool_calls:
                deferred_inputs.append(claimed_message)
            else:
                messages.append(claimed_message)
            event_assistant_texts.clear()
            continue
        if event_type == "context_window_reset":
            if not isinstance(data, dict):
                raise InvalidSessionSurfaceError("invalid context window event data")
            source_entry_ids = data.get("source_entry_ids")
            expected_source_ids = [item.id for item in records[:record_index]]
            if source_entry_ids != expected_source_ids:
                raise InvalidSessionSurfaceError(
                    "context window source entry IDs do not match the branch prefix"
                )
            if "rollover_spec" in data:
                if "replacement" in data:
                    raise InvalidSessionSurfaceError(
                        "a window reset cannot contain both a recipe and replacement"
                    )
                window_id = data.get("window_id")
                if not isinstance(window_id, str) or not window_id:
                    raise InvalidSessionSurfaceError("missing window ID")
                # 工具预览不是事实；先恢复完整产物，再选取新工作集。
                messages = _hydrate_tool_artifacts(
                    messages, tool_artifacts, artifacts_dir
                )
                try:
                    messages = apply_rollover_spec(
                        messages, data["rollover_spec"], window_id
                    )
                    validate_tool_pairing(messages)
                except (ValueError, TypeError) as exc:
                    raise InvalidSessionSurfaceError(
                        "invalid replayable context window recipe"
                    ) from exc
            elif "replacement" in data:
                # 保持既有 schema 2/3 日志的原始含义。
                messages = decode_surface_messages(data["replacement"])
            else:
                raise InvalidSessionSurfaceError(
                    "context window event lacks recipe or replacement"
                )
            raw_generation = data.get("generation")
            if not isinstance(raw_generation, int) or raw_generation <= generation:
                raise InvalidSessionSurfaceError(
                    "context window surface generation must increase"
                )
            generation = raw_generation
            replacement_entry_id = record.id
            pending_tool_calls.clear()
            open_tool_calls.clear()
            deferred_inputs.clear()
            seen_tool_call_ids = _tool_call_ids(messages)
            tool_artifacts.clear()
            event_assistant_texts.clear()
            continue

        event = _transcript_event(data, str(event_type))
        if isinstance(event, _AssistantEvent):
            before = len(messages)
            _append_tool_assistant(
                messages,
                event,
                pending_tool_calls,
                seen_tool_call_ids,
            )
            if len(messages) > before:
                open_tool_calls.update(_tool_call_ids(messages[-1:]))
            event_assistant_texts.append("".join(event.texts).strip())
        elif isinstance(event, ToolCallContent):
            if event.id not in seen_tool_call_ids:
                pending_tool_calls.append(event)
                open_tool_calls.add(event.id)
                seen_tool_call_ids.add(event.id)
        elif isinstance(event, ToolResultMessage):
            if isinstance(data, dict) and data.get("content_artifact") is not None:
                tool_artifacts[event.tool_call_id] = data["content_artifact"]
            _append_tool_result(messages, event, pending_tool_calls)
            open_tool_calls.discard(event.tool_call_id)
            if not open_tool_calls:
                messages.extend(deferred_inputs)
                deferred_inputs.clear()

    # 崩溃可能留下未完成工具批次，已 claim 的输入不能因此消失。
    messages.extend(deferred_inputs)
    messages = _hydrate_tool_artifacts(messages, tool_artifacts, artifacts_dir)
    return SessionSurface(
        messages=tuple(messages),
        generation=generation,
        replacement_entry_id=replacement_entry_id,
    )


def _hydrate_tool_artifacts(
    messages: list[AgentMessage],
    tool_artifacts: dict[str, object],
    artifacts_dir: Path | None,
) -> list[AgentMessage]:
    """用完整工具结果重放，不用日志中的有界预览。"""
    result = list(messages)
    for index, message in enumerate(result):
        if not isinstance(message, ToolResultMessage):
            continue
        reference = tool_artifacts.get(message.tool_call_id)
        if reference is None:
            continue
        content = resolve_tool_result_artifact(artifacts_dir, reference)
        if content is None:
            raise InvalidSessionSurfaceError(
                "Tool result artifact is missing or invalid; preview is not replayable."
            )
        result[index] = message.model_copy(update={"content": content})
    return result


def validate_tool_pairing(messages: list[AgentMessage]) -> None:
    """确保 replacement 不会切开 assistant tool call/result 对。"""
    pending: set[str] = set()
    completed: set[str] = set()
    for message in messages:
        if pending and not isinstance(message, ToolResultMessage):
            raise InvalidSessionSurfaceError(
                "tool results must immediately follow assistant tool calls"
            )
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if not isinstance(block, ToolCallContent):
                    continue
                if block.id in pending or block.id in completed:
                    raise InvalidSessionSurfaceError(
                        f"duplicate tool call in surface: {block.id}"
                    )
                pending.add(block.id)
        elif isinstance(message, ToolResultMessage):
            if message.tool_call_id not in pending:
                raise InvalidSessionSurfaceError(
                    f"orphan tool result in surface: {message.tool_call_id}"
                )
            pending.remove(message.tool_call_id)
            completed.add(message.tool_call_id)
    if pending:
        unresolved = ", ".join(sorted(pending))
        raise InvalidSessionSurfaceError(
            f"surface ends with unresolved tool calls: {unresolved}"
        )


@dataclass(frozen=True)
class _AssistantEvent:
    content: tuple[object, ...]
    tool_call_ids: frozenset[str]
    texts: tuple[str, ...]
    reasoning_content: str | None = None
    replay_payload: ProviderReplayPayload | None = None


def _message_kind(message: AgentMessage) -> str:
    if isinstance(message, SystemMessage):
        return "system"
    if isinstance(message, UserMessage):
        return "user"
    if isinstance(message, ContextMessage):
        return "context"
    if isinstance(message, AssistantMessage):
        return "assistant"
    if isinstance(message, ToolResultMessage):
        return "tool_result"
    return "branch_summary"


def _append_plain_assistant(
    messages: list[AgentMessage],
    record: SessionEntry,
    event_texts: list[str],
) -> None:
    from cade.agent.types import TextContent

    text = str(record.content).strip()
    # 最终记录通常重复最后一个模型响应，旧记录也可能拼接整个回合文本。
    candidates = ["\n\n".join(event_texts).strip()]
    if event_texts:
        candidates.append(event_texts[-1])
    event_text = next(
        (
            candidate
            for candidate in sorted(candidates, key=len, reverse=True)
            if candidate
            and (
                text == candidate
                or (
                    text.startswith(candidate)
                    and text[len(candidate) : len(candidate) + 1].isspace()
                )
            )
        ),
        "",
    )
    remaining = text[len(event_text) :].strip() if event_text else text
    if remaining:
        messages.append(AssistantMessage(content=[TextContent(text=remaining)]))


def _transcript_event(data: object, event_type: str) -> object | None:
    if event_type == "assistant":
        return _assistant_event(data)
    if event_type == "tool_use":
        return _tool_call(data)
    if event_type == "tool_result":
        return _tool_result(data)
    return None


def _assistant_event(data: object) -> _AssistantEvent | None:
    from cade.agent.types import ContentBlock, TextContent

    reasoning_content: str | None = None
    replay_payload: ProviderReplayPayload | None = None
    if isinstance(data, dict):
        raw_blocks = data.get("blocks")
        if not isinstance(raw_blocks, list):
            return None
        blocks = raw_blocks
        raw_reasoning = data.get("reasoning_content")
        if isinstance(raw_reasoning, str):
            reasoning_content = raw_reasoning
        raw_replay = data.get("replay_payload")
        if raw_replay is not None:
            try:
                replay_payload = AssistantMessage.model_validate(
                    {"replay_payload": raw_replay}
                ).replay_payload
            except ValidationError as exc:
                raise InvalidSessionSurfaceError(
                    "assistant replay payload is invalid"
                ) from exc
    elif isinstance(data, list):
        blocks = data
    else:
        return None
    content: list[ContentBlock] = []
    ids: set[str] = set()
    texts: list[str] = []
    for raw in blocks:
        if not isinstance(raw, dict):
            continue
        if raw.get("type") == "text":
            text = str(raw.get("text", ""))
            if text:
                content.append(TextContent(text=text))
                texts.append(text)
            continue
        call = _tool_call(raw)
        if call is not None:
            content.append(call)
            ids.add(call.id)
    if not content and replay_payload is None:
        return None
    return _AssistantEvent(
        tuple(content),
        frozenset(ids),
        tuple(texts),
        reasoning_content,
        replay_payload,
    )


def _tool_call(data: object) -> ToolCallContent | None:
    if not isinstance(data, dict):
        return None
    tool_call_id = str(data.get("id", "")).strip()
    name = str(data.get("name", "")).strip()
    arguments = data.get("input", {})
    if not tool_call_id or not name or not isinstance(arguments, dict):
        return None
    return ToolCallContent(id=tool_call_id, name=name, arguments=arguments)


def _tool_result(data: object) -> ToolResultMessage | None:
    from cade.agent.types import TextContent, parse_tool_render_intent

    if not isinstance(data, dict):
        return None
    tool_call_id = str(data.get("tool_use_id", "")).strip()
    if not tool_call_id:
        return None
    status = str(data.get("status", "ok"))
    return ToolResultMessage(
        tool_call_id=tool_call_id,
        content=[TextContent(text=str(data.get("content", "")))],
        is_error=status != "ok",
        metadata=(
            dict(data["metadata"]) if isinstance(data.get("metadata"), dict) else None
        ),
        render_intent=parse_tool_render_intent(data.get("render_intent")),
    )


def _append_tool_assistant(
    messages: list[AgentMessage],
    event: _AssistantEvent,
    pending: list[ToolCallContent],
    seen: set[str],
) -> None:
    from cade.agent.types import ContentBlock

    inline_calls = [
        block
        for block in event.content
        if isinstance(block, ToolCallContent) and block.id not in seen
    ]
    calls = [*pending, *inline_calls]
    if not calls and event.tool_call_ids:
        return
    content = [
        block for block in event.content if not isinstance(block, ToolCallContent)
    ]
    content.extend(calls)
    messages.append(
        AssistantMessage(
            content=cast(list[ContentBlock], content),
            reasoning_content=event.reasoning_content,
            replay_payload=event.replay_payload,
        )
    )
    seen.update(call.id for call in calls)
    pending.clear()


def _append_tool_result(
    messages: list[AgentMessage],
    result: ToolResultMessage,
    pending: list[ToolCallContent],
) -> None:
    if pending:
        messages.append(AssistantMessage(content=list(pending)))
        pending.clear()
    name = next(
        (
            block.name
            for message in reversed(messages)
            if isinstance(message, AssistantMessage)
            for block in message.content
            if isinstance(block, ToolCallContent) and block.id == result.tool_call_id
        ),
        "",
    )
    messages.append(result.model_copy(update={"tool_name": name}))


def _tool_call_ids(messages: list[AgentMessage]) -> set[str]:
    return {
        block.id
        for message in messages
        if isinstance(message, AssistantMessage)
        for block in message.content
        if isinstance(block, ToolCallContent)
    }
