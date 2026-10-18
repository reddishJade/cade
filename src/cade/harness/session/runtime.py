"""运行时使用的会话事实、输入队列和投影入口。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import filelock

from .inbox import SessionInbox
from .recorder import SessionRecorder


class RuntimeSession:
    """让输入、记录器和历史投影共享同一个事实存储。"""

    def __init__(self, recorder: SessionRecorder) -> None:
        self.recorder = recorder
        self.inbox = SessionInbox(recorder.store)

    @contextmanager
    def run_lock(self) -> Iterator[None]:
        """拒绝同一持久会话的并发运行，不阻塞异步事件循环。"""
        path = self.recorder.store.current_path
        lock = filelock.FileLock(str(path.with_suffix(".run.lock")), timeout=0)
        try:
            lock.acquire()
        except filelock.Timeout as exc:
            raise RuntimeError("session already has an active run") from exc
        try:
            yield
        finally:
            lock.release()
