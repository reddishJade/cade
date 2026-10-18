"""由运行时拥有的 session 记录器。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Protocol, cast
from weakref import WeakValueDictionary

from cade.agent.context_policy import ContextPolicy
from cade.agent.messages import AgentMessage
from cade.harness.agent_runtime.events import (
    AgentHarnessEvent,
    ContextWindowResetStructuredEvent,
    FinalStructuredEvent,
    ToolResultStructuredEvent,
    is_model_fact_event,
)

from .artifacts import offload_large_tool_result
from .event_codec import SESSION_EVENT_SCHEMA_VERSION, encode_session_event
from .store import SessionStore
from .subagent_runs import (
    SubagentActivationEvent,
    SubagentDescriptor,
    SubagentRunEvent,
)
from .surface import encode_surface_messages
from .types import JsonValue, SessionEntry
from .window_projection import encode_rollover_spec

_DURABLE_PROVIDER_REQUEST_FIELDS = (
    "provider",
    "options",
    "composition_id",
    "assembly",
    "prompt_version",
    "prompt_digest",
    "request_digest",
    "request_bytes",
    "system_prompt_bytes",
    "budget_reminder_present",
)


class SessionBoundAgent(Protocol):
    @property
    def session_id(self) -> str: ...

    @session_id.setter
    def session_id(self, value: str) -> None: ...

    def set_history_session_id(self, session_id: str) -> None: ...


class ProviderRequestRecord(Protocol):
    @property
    def metadata(self) -> Mapping[str, object] | None: ...

    @property
    def timestamp(self) -> str: ...

    @property
    def session_id(self) -> str: ...

    @property
    def turn_id(self) -> str: ...

    @property
    def request_id(self) -> str: ...


class SessionRecorder:
    """记录一次运行产生的稳定语义事件，并绑定 agent 的 session 身份。"""

    def __init__(
        self,
        store: SessionStore,
        context_window_state_provider: Callable[[], Mapping[str, object] | None]
        | None = None,
    ) -> None:
        self.store = store
        self._context_window_state_provider = context_window_state_provider
        self._recorded_events: WeakValueDictionary[int, AgentHarnessEvent] = (
            WeakValueDictionary()
        )

    def bind_agent(
        self,
        agent: SessionBoundAgent,
    ) -> None:
        session_id = self.store.session_id
        agent.session_id = session_id
        agent.set_history_session_id(session_id)

    def record_model_event(self, record: ProviderRequestRecord) -> None:
        """在生产事件的线程同步记录事实，不依赖 UI 是否消费事件。"""
        if record.metadata is not None:
            self.record_event(cast(AgentHarnessEvent, record.metadata["event"]))

    def record_event(self, event: AgentHarnessEvent) -> None:
        if not is_model_fact_event(event) and not isinstance(
            event, FinalStructuredEvent
        ):
            return
        if self._recorded_events.get(id(event)) is event:
            return
        encoded = encode_session_event(event)
        if isinstance(event, ToolResultStructuredEvent):
            data = encoded.get("data")
            if not isinstance(data, dict):
                raise TypeError("tool result event data must be an object")
            preview, reference = offload_large_tool_result(
                self.store.artifacts_dir,
                event.data.content,
            )
            data["content"] = preview
            if reference is not None:
                data["content_artifact"] = reference
        if isinstance(event, ContextWindowResetStructuredEvent):
            data = encoded.get("data")
            if not isinstance(data, dict):
                raise TypeError("context window event data must be an object")
            restoration_context = self._context_window_state()
            if restoration_context is not None:
                data["restoration_context"] = restoration_context
            self._append_surface_event(encoded, data)
        else:
            self.store.append("event", encoded)
        self._recorded_events[id(event)] = event
        if isinstance(event, FinalStructuredEvent) and event.data.answer:
            # 展示用回答由 final 事件承载；保留对旧日志和中断存档中
            # 独立 assistant 记录的读取。
            self.store.update_summary()

    def record_assistant(self, text: str) -> None:
        if not text.strip():
            return
        self.store.append("assistant", text)
        self.store.update_summary()

    def record_context_window_reset(
        self,
        *,
        window_id: str,
        messages_before: int,
        messages_after: int,
        replacement: list[AgentMessage],
        reproject_from_facts: bool = False,
        rollover_policy: ContextPolicy | None = None,
        preserve_user_request: bool = True,
    ) -> str:
        """追加换窗决策；内置策略可从日志事实重建工作窗口。"""
        data: dict[str, JsonValue] = {
            "window_id": window_id,
            "trigger": "manual",
            "messages_before": messages_before,
            "messages_after": messages_after,
        }
        if reproject_from_facts:
            data["rollover_spec"] = encode_rollover_spec(
                rollover_policy,
                preserve_user_request=preserve_user_request,
            )
        else:
            data["replacement"] = encode_surface_messages(replacement)
        restoration_context = self._context_window_state()
        if restoration_context is not None:
            data["restoration_context"] = restoration_context
        return self._append_surface_event(
            {
                "schema_version": SESSION_EVENT_SCHEMA_VERSION,
                "type": "context_window_reset",
                "step": 0,
                "data": data,
                "correlation": {},
            },
            data,
        )

    def _context_window_state(self) -> dict[str, JsonValue] | None:
        provider = self._context_window_state_provider
        if provider is None:
            return None
        state = provider()
        if not state:
            return None
        encoded = _json_value(state)
        if not isinstance(encoded, dict):
            raise TypeError("context window restoration state must be an object")
        return encoded

    def _append_surface_event(
        self, event: dict[str, JsonValue], data: dict[str, JsonValue]
    ) -> str:
        def content(branch: list[SessionEntry]) -> JsonValue:
            data.update(self._surface_metadata(branch))
            return event

        return self.store.append_derived("event", content)

    @staticmethod
    def _surface_metadata(branch: list[SessionEntry]) -> dict[str, JsonValue]:
        generations = [
            data.get("generation", 0)
            for entry in branch
            if entry.type == "event"
            and isinstance(entry.content, dict)
            and entry.content.get("type") == "context_window_reset"
            and isinstance(data := entry.content.get("data"), dict)
        ]
        generation = 1 + max(
            (value for value in generations if isinstance(value, int)), default=0
        )
        return {
            "generation": generation,
            "source_entry_ids": [entry.id for entry in branch],
        }

    def record_provider_request(self, record: ProviderRequestRecord) -> None:
        """保存可审计请求指纹，不在 session 中重复完整 wire payload。"""
        metadata = record.metadata or {}
        durable = {
            key: metadata[key]
            for key in _DURABLE_PROVIDER_REQUEST_FIELDS
            if key in metadata
        }
        messages = metadata.get("messages")
        tools = metadata.get("tools")
        durable["message_count"] = len(messages) if isinstance(messages, list) else 0
        durable["tool_count"] = len(tools) if isinstance(tools, list) else 0
        self.store.append(
            "event",
            {
                "schema_version": SESSION_EVENT_SCHEMA_VERSION,
                "type": "provider_request",
                "step": 0,
                "data": _json_value(durable),
                "correlation": {
                    "timestamp": record.timestamp,
                    "session_id": record.session_id,
                    "turn_id": record.turn_id,
                    "request_id": record.request_id,
                    "tool_call_id": "",
                },
            },
        )

    def record_subagent_run(self, event: SubagentRunEvent) -> str:
        """追加子代理生命周期事件，并记录父 session 归属。"""
        data = event.model_dump()
        data["parent_session_id"] = self.store.session_id
        return self.store.append(
            "event",
            {
                "schema_version": SESSION_EVENT_SCHEMA_VERSION,
                "type": "subagent_run",
                "step": 0,
                "data": data,
                "correlation": {},
            },
        )

    def record_subagent_descriptor(self, descriptor: SubagentDescriptor) -> str:
        """在 child session 写入不进入模型 surface 的稳定 descriptor。"""
        return self.store.append(
            "event",
            {
                "schema_version": SESSION_EVENT_SCHEMA_VERSION,
                "type": "subagent/descriptor",
                "step": 0,
                "data": descriptor.model_dump(),
                "correlation": {},
            },
        )

    def record_subagent_activation(self, event: SubagentActivationEvent) -> str:
        """在 child session 记录一次 process-local activation epoch。"""
        return self.store.append(
            "event",
            {
                "schema_version": SESSION_EVENT_SCHEMA_VERSION,
                "type": "subagent/activation",
                "step": 0,
                "data": event.model_dump(),
                "correlation": {},
            },
        )


def _json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_value(item) for item in value]
    raise TypeError(f"provider request contains non-JSON value: {type(value).__name__}")
