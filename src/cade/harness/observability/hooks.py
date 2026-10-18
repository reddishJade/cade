from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal, Protocol

from blinker import Signal

"""基于 HookRecord 的同步与后台生命周期回调。"""

logger = logging.getLogger("cade.harness.observability.hooks")

HookEvent = Literal[
    "pre_tool",
    "post_tool",
    "on_error",
    "on_context_window_reset",
    "before_agent_start",
    "before_provider_request",
    "on_agent_event",
]
HookCallback = Callable[["HookRecord"], None]

_HOOK_EVENTS: tuple[HookEvent, ...] = (
    "pre_tool",
    "post_tool",
    "on_error",
    "on_context_window_reset",
    "before_agent_start",
    "before_provider_request",
    "on_agent_event",
)


@dataclass(frozen=True)
class HookRecord:
    event: HookEvent
    tool: str = ""
    input: str = ""
    output: str = ""
    error: str = ""
    metadata: Mapping[str, object] | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    session_id: str = ""
    turn_id: str = ""
    request_id: str = ""
    tool_call_id: str = ""


class HookManager(Protocol):
    def register(self, event: HookEvent, callback: HookCallback) -> None: ...
    def remove(self, event: HookEvent, callback: HookCallback) -> None: ...
    def register_background(self, event: HookEvent, callback: HookCallback) -> None: ...
    def remove_background(self, event: HookEvent, callback: HookCallback) -> None: ...
    def emit(self, record: HookRecord) -> None: ...
    def drain_background(self) -> None: ...


class SignalHookManager:
    def __init__(self) -> None:
        self._registered: dict[HookEvent, Signal] = {
            event: Signal() for event in _HOOK_EVENTS
        }
        self._background: dict[HookEvent, Signal] = {
            event: Signal() for event in _HOOK_EVENTS
        }
        self._background_queue: queue.Queue[tuple[HookEvent, HookRecord]] = (
            queue.Queue()
        )
        self._worker_started = False
        self._worker_lock = threading.Lock()

    def register(self, event: HookEvent, callback: HookCallback) -> None:
        self._registered[event].connect(callback, weak=False)

    def remove(self, event: HookEvent, callback: HookCallback) -> None:
        self._registered[event].disconnect(callback)

    def register_background(self, event: HookEvent, callback: HookCallback) -> None:
        self._background[event].connect(callback, weak=False)

    def remove_background(self, event: HookEvent, callback: HookCallback) -> None:
        self._background[event].disconnect(callback)

    def emit(self, record: HookRecord) -> None:
        self._registered[record.event].send(record)
        if self._background[record.event].receivers:
            self._ensure_worker()
            self._background_queue.put((record.event, record))

    def drain_background(self) -> None:
        """等待已入队后台 hook 完成，供测试和受控关闭使用。"""
        self._background_queue.join()

    def _ensure_worker(self) -> None:
        with self._worker_lock:
            if self._worker_started:
                return
            thread = threading.Thread(
                target=self._run_background,
                name="cade-hook-manager",
                daemon=True,
            )
            thread.start()
            self._worker_started = True

    def _run_background(self) -> None:
        while True:
            event, record = self._background_queue.get()
            try:
                self._send_background(event, record)
            finally:
                self._background_queue.task_done()

    def _send_background(self, event: HookEvent, record: HookRecord) -> None:
        for receiver in self._background[event].receivers_for(record):
            try:
                receiver(record)
            except Exception:
                logger.exception("background hook failed for event %s", event)
