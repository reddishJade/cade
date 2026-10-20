"""验证兼容 Provider 的请求隔离、流结束与单流取消。

使用公开 Chat Completions chunk 形状驱动真实适配器。阻塞流在 close 后退出，
独立检查请求头和另一条连接，防止取消一个请求时关闭其他请求。
无需凭据；运行 pytest -q 本文件。
"""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from typing import Any

import pytest

from cade.ai.events import FinalMessage, UsageUpdate
from cade.ai.providers._stream import chat_stream_to_events
from cade.ai.providers.openai import OpenAIChatProvider
from cade.ai.types import ProviderConfig, StreamOptions


@pytest.mark.parametrize(
    "reason,expected",
    [("stop", "end_turn"), ("length", "max_tokens"), ("content_filter", "error")],
)
def test_chat_finish_reason_is_explicit(reason: str, expected: str) -> None:
    chunk = SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=120,
            completion_tokens=8,
            prompt_tokens_details=SimpleNamespace(cached_tokens=100),
        ),
        choices=[
            SimpleNamespace(
                finish_reason=reason,
                delta=SimpleNamespace(content="answer", tool_calls=None),
            )
        ],
    )
    events = list(chat_stream_to_events([chunk]))
    assert UsageUpdate(120, 8, 100) in events
    assert events[-1] == FinalMessage("answer", expected)


def test_truncated_chat_stream_has_no_success_marker() -> None:
    chunk = SimpleNamespace(
        usage=None,
        choices=[
            SimpleNamespace(
                finish_reason=None,
                delta=SimpleNamespace(content="partial", tool_calls=None),
            )
        ],
    )
    assert not any(
        isinstance(event, FinalMessage) for event in chat_stream_to_events([chunk])
    )


class _BlockedStream:
    def __init__(self) -> None:
        self.closed = threading.Event()
        self.reading = threading.Event()
        self.started = False

    def __iter__(self) -> _BlockedStream:
        return self

    def __next__(self) -> Any:
        if not self.started:
            self.started = True
            return SimpleNamespace(
                usage=None,
                choices=[
                    SimpleNamespace(
                        finish_reason=None,
                        delta=SimpleNamespace(content="started", tool_calls=None),
                    )
                ],
            )
        self.reading.set()
        self.closed.wait(3)
        raise StopIteration

    def close(self) -> None:
        self.closed.set()


@pytest.mark.asyncio
async def test_cancelling_one_chat_stream_does_not_close_the_other() -> None:
    streams: dict[str, _BlockedStream] = {}

    def create(**params: Any) -> _BlockedStream:
        stream = _BlockedStream()
        streams[params["extra_headers"]["x-session-id"]] = stream
        return stream

    provider = OpenAIChatProvider(
        ProviderConfig(api_key="", model="test", thinking=False),
        client=SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        ),
    )

    async def consume(identity: str) -> None:
        async for _ in provider.stream(
            [{"role": "user", "content": identity}],
            [],
            StreamOptions(session_id=identity),
        ):
            pass

    first = asyncio.create_task(consume("one"))
    second = asyncio.create_task(consume("two"))
    try:
        async with asyncio.timeout(2):
            while len(streams) < 2 or not all(
                stream.reading.is_set() for stream in streams.values()
            ):
                await asyncio.sleep(0.005)
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        assert streams["one"].closed.is_set()
        assert not streams["two"].closed.is_set()
        assert not second.done()
    finally:
        first.cancel()
        second.cancel()
        await asyncio.gather(first, second, return_exceptions=True)
