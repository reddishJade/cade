"""Session-backed agent inbox 与 durable 生命周期投影。"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from threading import RLock
from typing import Literal, cast
from uuid import uuid4

from cade.agent.messages import AgentMessage, UserMessage

from .schema import SESSION_EVENT_SCHEMA_VERSION, supports_session_event_schema
from .store import SessionStore
from .surface import decode_surface_messages, encode_surface_messages
from .types import JsonValue, SessionEntry


class InboxLane(StrEnum):
    """输入在何时进入模型 step。"""

    NEXT_TURN = "next_turn"
    NEXT_STEP = "next_step"


type InboxSource = Literal["user", "runtime", "subagent"]


@dataclass(frozen=True)
class InboxItem:
    """尚未被模型消费的一条输入。"""

    id: str
    lane: InboxLane
    message: AgentMessage
    source: InboxSource
    display_text: str
    wake: bool


class SessionInbox:
    """把 pending input 与其生命周期保存在 session branch。"""

    def __init__(self, store: SessionStore) -> None:
        self._store = store
        self._lock = RLock()
        self._items: deque[InboxItem] = deque()
        self.reload()

    @property
    def session_id(self) -> str:
        return self._store.session_id

    def reload(self) -> None:
        """从当前 branch 重建尚未 claim/discard 的输入。"""
        with self._lock:
            self._items = self._pending_from_store()

    def insert(
        self,
        message: AgentMessage,
        lane: InboxLane,
        *,
        source: InboxSource = "user",
        display_text: str | None = None,
        wake: bool,
    ) -> InboxItem:
        """追加一条输入并持久化 inserted event。"""
        text = _message_text(message)
        item = InboxItem(
            id=uuid4().hex,
            lane=lane,
            message=message,
            source=source,
            display_text=display_text if display_text is not None else text,
            wake=wake,
        )
        with self._lock:
            self._store.ensure_metadata(item.display_text)
            self._append_event(_event("inbox/inserted", item))
            self._items.append(item)
        return item

    def claim_runtime(self, message: UserMessage, run_id: str) -> UserMessage:
        """记录并立即消费内部输入，不取走同时到达的 steering。"""
        with self._lock:
            item = self.insert(
                message, InboxLane.NEXT_STEP, source="runtime", wake=False
            )
            self._record_claimed([item], run_id)
        return message.model_copy(update={"source": "runtime"})

    def claim_initial(self, run_id: str) -> list[AgentMessage]:
        """在 turn 边界 claim next-step 输入和一条 next-turn 输入。"""
        with self._lock:
            claimed: list[InboxItem] = []
            turn_claimed = False
            for item in self._items:
                if item.lane is InboxLane.NEXT_STEP:
                    claimed.append(item)
                elif not turn_claimed:
                    claimed.append(item)
                    turn_claimed = True
            self._record_claimed(claimed, run_id)
        return [_claimed_message(item) for item in claimed]

    def claim_next_step(self, run_id: str) -> list[AgentMessage]:
        """claim 当前所有 next-step 输入。"""
        with self._lock:
            claimed = [item for item in self._items if item.lane is InboxLane.NEXT_STEP]
            self._record_claimed(claimed, run_id)
        return [_claimed_message(item) for item in claimed]

    def discard_all(self, reason: str) -> None:
        """显式丢弃当前 session 的所有 pending input。"""
        with self._lock:
            discarded = list(self._items)
            for item in discarded:
                self._append_event(_event("inbox/discarded", item, reason=reason))
                self._items.remove(item)

    def has_waking_input(self) -> bool:
        with self._lock:
            return bool(self._items)

    def pending_count(self) -> int:
        with self._lock:
            return len(self._items)

    def _record_claimed(self, items: list[InboxItem], run_id: str) -> None:
        for item in items:
            self._append_event(_event("inbox/claimed", item, run_id=run_id))
            self._items.remove(item)

    def _append_event(self, event: dict[str, JsonValue]) -> None:
        """持有 inbox 锁时提交；写入结果不确定时从事实重建缓存。"""
        try:
            self._store.append("event", event)
        except (OSError, RuntimeError, TypeError, ValueError):
            self._items = self._pending_from_store()
            raise

    def _pending_from_store(self) -> deque[InboxItem]:
        return _pending_items(
            self._store.read_view().facts(
                "inbox/inserted", "inbox/claimed", "inbox/discarded"
            )
        )


def _pending_items(records: list[SessionEntry]) -> deque[InboxItem]:
    """纯投影：只有 inserted 且未 claimed/discarded 的输入仍待处理。"""
    pending: dict[str, InboxItem] = {}
    order: list[str] = []
    for entry in records:
        if entry.type != "event" or not isinstance(entry.content, dict):
            continue
        event_type = entry.content.get("type")
        if str(event_type).startswith("inbox/") and not supports_session_event_schema(
            entry.content.get("schema_version")
        ):
            raise ValueError("unsupported inbox event schema")
        data = entry.content.get("data")
        if not isinstance(data, dict):
            continue
        item_id = str(data.get("inbox_id", ""))
        if not item_id:
            continue
        if event_type == "inbox/inserted":
            pending[item_id] = _item_from_data(data)
            order.append(item_id)
        elif event_type in {"inbox/claimed", "inbox/discarded"}:
            pending.pop(item_id, None)
    return deque(pending[item_id] for item_id in order if item_id in pending)


def _claimed_message(item: InboxItem) -> AgentMessage:
    """按 inbox 持久化来源标注消息，不按 role 文本推断。"""
    if isinstance(item.message, UserMessage):
        return item.message.model_copy(update={"source": item.source})
    return item.message


def inbox_display_text(content: object) -> str | None:
    """读取 claimed 用户输入的宿主展示文本。"""
    if not isinstance(content, dict) or content.get("type") != "inbox/claimed":
        return None
    if not supports_session_event_schema(content.get("schema_version")):
        return None
    data = content.get("data")
    if not isinstance(data, dict):
        return None
    message = data.get("message")
    if (
        not isinstance(message, list)
        or len(message) != 1
        or not isinstance(message[0], dict)
        or message[0].get("kind") != "user"
    ):
        return None
    value = data.get("display_text")
    return value if isinstance(value, str) else None


def _event(
    event_type: str,
    item: InboxItem,
    *,
    run_id: str = "",
    reason: str = "",
) -> dict[str, JsonValue]:
    return {
        "schema_version": SESSION_EVENT_SCHEMA_VERSION,
        "type": event_type,
        "step": 0,
        "data": {
            "inbox_id": item.id,
            "lane": item.lane.value,
            "message": encode_surface_messages([item.message]),
            "source": item.source,
            "display_text": item.display_text,
            "wake": item.wake,
            "run_id": run_id,
            "reason": reason,
        },
        "correlation": {},
    }


def _item_from_data(data: Mapping[str, object]) -> InboxItem:
    lane_value = data.get("lane")
    source_value = data.get("source")
    if lane_value not in {lane.value for lane in InboxLane}:
        raise ValueError(f"invalid inbox lane: {lane_value}")
    if source_value not in {"user", "runtime", "subagent"}:
        raise ValueError(f"invalid inbox source: {source_value}")
    return InboxItem(
        id=str(data["inbox_id"]),
        lane=InboxLane(str(lane_value)),
        message=_decode_message(data.get("message")),
        source=cast(InboxSource, source_value),
        display_text=str(data.get("display_text", "")),
        wake=bool(data.get("wake", False)),
    )


def _decode_message(value: object) -> AgentMessage:
    messages = decode_surface_messages(value)
    if len(messages) != 1:
        raise ValueError("inbox event must contain exactly one message")
    return messages[0]


def _message_text(message: AgentMessage) -> str:
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    return "\n".join(str(getattr(block, "text", "")) for block in content or []).strip()
