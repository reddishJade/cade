"""Agent 流消费者取消语义测试。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from xcode.agent.agent import Agent
from xcode.agent.config import AgentLoopConfig
from xcode.agent.messages import UserMessage
from xcode.ai.events import FinalMessage, ProviderEvent, TextDelta
from xcode.ai.types import StreamOptions, ToolDefinition


class _BlockingProvider:
    @property
    def model(self) -> str:
        return "blocking-model"

    async def stream(
        self,
        messages: list[dict[str, object]],
        tools: list[ToolDefinition],
        options: StreamOptions | None = None,
        **_kwargs: object,
    ) -> AsyncIterator[ProviderEvent]:
        del messages, tools, options
        yield TextDelta("partial")
        await asyncio.Event().wait()
        yield FinalMessage(content="", stop_reason="end_turn")


async def test_closing_stream_does_not_leak_internal_cancellation() -> None:
    provider = _BlockingProvider()
    agent = Agent(tools=[], model=provider)
    stream = agent.run_stream(
        [UserMessage(content="continue")],
        AgentLoopConfig(provider=provider),
    )

    await anext(stream)
    await asyncio.wait_for(stream.aclose(), timeout=1)


class _UnexpectedStreamError(Exception):
    pass


async def test_run_stream_retrieves_and_propagates_background_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fail_loop(*_args: object, **_kwargs: object) -> None:
        raise _UnexpectedStreamError("background failure")

    monkeypatch.setattr("xcode.agent.agent.run_agent_loop", _fail_loop)
    provider = _BlockingProvider()
    agent = Agent(tools=[], model=provider)

    with pytest.raises(_UnexpectedStreamError, match="background failure"):
        async for _event in agent.run_stream(
            [UserMessage(content="continue")],
            AgentLoopConfig(provider=provider),
        ):
            pass
