"""同步/异步桥的资源所有权不变量测试。"""

from __future__ import annotations

import asyncio
import threading

from cade.harness.agent_runtime.agent_helpers import aiter_to_sync_iter
from cade.harness.agent_runtime.cancellation import CancellationToken
from cade.harness.agent_runtime.events import TextDeltaStructuredEvent


def test_sync_stream_finalizes_generator_on_its_owning_loop() -> None:
    observed: dict[str, object] = {}

    async def stream():
        observed["created_loop"] = asyncio.get_running_loop()
        observed["created_thread"] = threading.get_ident()
        try:
            yield TextDeltaStructuredEvent("text_delta", 1, "first")
            await asyncio.Event().wait()
        finally:
            observed["closed_loop"] = asyncio.get_running_loop()
            observed["closed_thread"] = threading.get_ident()

    iterator = aiter_to_sync_iter(stream(), CancellationToken())
    assert next(iterator).data == "first"

    iterator.close()

    assert observed["closed_loop"] is observed["created_loop"]
    assert observed["closed_thread"] == observed["created_thread"]
