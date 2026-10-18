"""运行时使用的会话事实、输入队列和投影入口。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from cade.agent.messages import AgentMessage
from cade.harness.agent_runtime.events import ToolResultBlock, ToolResultStructuredEvent

from .inbox import SessionInbox
from .recorder import SessionRecorder
from .replay import unresolved_tool_attempts
from .surface import project_session_surface


class RuntimeSession:
    """让输入、记录器和历史投影共享同一个事实存储。"""

    def __init__(self, recorder: SessionRecorder) -> None:
        self.recorder = recorder
        self.inbox = SessionInbox(recorder.store)

    def recover_tool_attempts(self) -> int:
        """把未知工具结果记录为恢复事实，禁止自动重放副作用。"""
        pending = unresolved_tool_attempts(self.recorder.store.build_branch())
        for call_id in pending:
            self.recorder.record_event(
                ToolResultStructuredEvent(
                    "tool_result",
                    0,
                    ToolResultBlock(
                        call_id,
                        "Execution outcome is UNKNOWN after interruption. "
                        "Inspect workspace and external state before retrying; "
                        "this recovery record does not prove execution failed.",
                        status="error",
                        metadata={
                            "execution_status": "interrupted",
                            "outcome": "unknown",
                        },
                    ),
                )
            )
        return len(pending)

    def history_messages(self) -> list[AgentMessage]:
        store = self.recorder.store
        surface = project_session_surface(
            store.build_branch(), artifacts_dir=store.artifacts_dir
        )
        return list(surface.messages)

    @contextmanager
    def run_lock(self) -> Iterator[None]:
        """拒绝同一持久会话的并发运行，不阻塞异步事件循环。"""
        with self.recorder.store.run_lock():
            yield
