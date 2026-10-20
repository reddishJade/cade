from __future__ import annotations

import asyncio
import concurrent.futures
import sys
import threading
from collections.abc import Coroutine
from typing import Any, TypeVar

T = TypeVar("T")


class IsolatedAsyncWorker:
    """在独立线程中持有事件循环，由 asyncio 调度提交的协程。"""

    def __init__(self, *, name: str = "cade-async-worker") -> None:
        self._name = name
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._started = threading.Event()
        self._closed = False
        self._lock = threading.Lock()

    def submit(
        self,
        coro: Coroutine[Any, Any, T],
    ) -> concurrent.futures.Future[T]:
        with self._lock:
            if self._closed:
                coro.close()
                raise RuntimeError("worker is closed")
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._thread_main,
                    name=self._name,
                    daemon=True,
                )
                self._thread.start()
                self._started.wait()
            loop = self._loop
            if loop is None:
                coro.close()
                raise RuntimeError("worker event loop failed to start")
            try:
                return asyncio.run_coroutine_threadsafe(coro, loop)
            except RuntimeError:
                coro.close()
                raise

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._closed = True
                if self._loop is not None:
                    self._loop.call_soon_threadsafe(self._loop.stop)
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5.0)

    def _thread_main(self) -> None:
        if sys.platform == "win32":
            loop = asyncio.SelectorEventLoop()
        else:
            loop = asyncio.new_event_loop()

        self._loop = loop
        asyncio.set_event_loop(loop)
        self._started.set()

        try:
            loop.run_forever()
        finally:
            with self._lock:
                self._closed = True
            try:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
                loop.run_until_complete(loop.shutdown_asyncgens())
            finally:
                loop.close()
