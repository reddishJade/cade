"""Provider 流式收集、取消与故障分类测试。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from cade.agent._provider import _collect_provider_events
from cade.agent.agent_loop import run_agent_loop
from cade.agent.config import AgentContext, AgentLoopConfig
from cade.agent.context_manager import ContextManager
from cade.agent.events import ContextWindowResetEvent
from cade.agent.messages import AssistantMessage, ToolResultMessage, UserMessage
from cade.agent.request import RequestAssembly
from cade.agent.results import AgentLoopResult, TerminationReason
from cade.agent.types import TextContent, ToolCallContent
from cade.ai.events import (
    FinalMessage,
    ProviderEvent,
    ProviderFailure,
    TextDelta,
)
from cade.ai.types import StreamOptions, ToolDefinition
from cade.harness.agent_runtime.cancellation import CancellationToken
from cade.harness.agent_runtime.result import build_structured_result


class _EndlessProvider:
    """无限产出文本增量，并记录中断中止调用。"""

    def __init__(self) -> None:
        self.abort_calls = 0

    def abort_active_stream(self) -> None:
        self.abort_calls += 1

    async def stream(
        self,
        messages: list[dict[str, object]],
        tools: list[object],
        options: object | None = None,
        **kwargs: object,
    ) -> object:
        while True:
            yield TextDelta(chunk="tok")
            await asyncio.sleep(0)


class _RaisingProvider:
    """流在首个事件前即抛出连接异常，模拟打断关闭连接的场景。"""

    def __init__(self) -> None:
        self.abort_calls = 0

    def abort_active_stream(self) -> None:
        self.abort_calls += 1

    async def stream(
        self,
        messages: list[dict[str, object]],
        tools: list[object],
        options: object | None = None,
        **kwargs: object,
    ) -> object:
        if False:  # pragma: no cover
            yield TextDelta(chunk="never")
        raise RuntimeError("connection reset by abort")


async def test_collect_returns_none_when_cancelled_mid_stream() -> None:
    """流式生成中途取消：返回 None 并中止在途请求。"""
    token = CancellationToken()
    provider = _EndlessProvider()

    async def cancel_soon() -> None:
        await asyncio.sleep(0.01)
        token.cancel("interrupted by user")

    canceller = asyncio.create_task(cancel_soon())
    events = await _collect_provider_events(
        provider, [], [], None, lambda _event: None, token
    )
    await canceller

    assert events is None
    assert provider.abort_calls >= 1


async def test_abort_exception_during_cancelled_stream_is_not_error() -> None:
    """打断关闭连接导致的流异常：已取消时按取消处理而非报错。"""
    token = CancellationToken()
    token.cancel("interrupted by user")
    provider = _RaisingProvider()

    events = await _collect_provider_events(
        provider, [], [], None, lambda _event: None, token
    )

    assert events is None


class _ShortProvider:
    async def stream(
        self,
        messages: list[dict[str, object]],
        tools: list[object],
        options: object | None = None,
        **kwargs: object,
    ) -> object:
        yield TextDelta(chunk="hello")


class _MaxTokensProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def stream(
        self,
        messages: list[dict[str, object]],
        tools: list[object],
        options: object | None = None,
        **kwargs: object,
    ) -> object:
        del messages, tools, options, kwargs
        self.calls += 1
        yield TextDelta(chunk="partial")
        yield FinalMessage(content="partial", stop_reason="max_tokens")


async def test_collect_returns_events_when_not_cancelled() -> None:
    """未取消时正常收集全部事件。"""
    events = await _collect_provider_events(
        _ShortProvider(), [], [], None, lambda _event: None, None
    )
    assert events == [TextDelta(chunk="hello")]


async def test_agent_loop_stops_before_exceeding_llm_call_limit() -> None:
    provider = _MaxTokensProvider()

    result = await run_agent_loop(
        [UserMessage(content="continue")],
        AgentContext(),
        AgentLoopConfig(provider=provider, max_llm_calls=1),
        lambda _event: None,
    )

    assert result.termination_reason is TerminationReason.LLM_CALL_LIMIT
    assert result.metrics is not None
    assert result.metrics.llm_calls == 1
    assert provider.calls == 1


async def test_agent_loop_terminates_when_interrupted_mid_stream() -> None:
    """端到端：模型流式生成期间被打断，循环以 CANCELLED 退出并中止流。"""
    provider = _EndlessProvider()
    token = CancellationToken()
    config = AgentLoopConfig(provider=provider)
    context = AgentContext(
        system_prompt="",
        messages=[UserMessage(content="hi")],
    )

    async def cancel_soon() -> None:
        await asyncio.sleep(0.05)
        token.cancel("interrupted by user")

    canceller = asyncio.create_task(cancel_soon())
    result = await run_agent_loop(
        [UserMessage(content="hi")],
        context,
        config,
        emit=lambda _event: None,
        signal=token,
    )
    await canceller

    assert result.termination_reason is TerminationReason.CANCELLED
    assert provider.abort_calls >= 1


class _ServiceUnavailable(RuntimeError):
    status_code = 503


class _Forbidden(RuntimeError):
    status_code = 403


class _FailingProvider:
    @property
    def model(self) -> str:
        return "failing-model"

    async def stream(
        self,
        messages: list[dict[str, object]],
        tools: list[ToolDefinition],
        options: StreamOptions | None = None,
        **_kwargs: object,
    ) -> AsyncIterator[ProviderEvent]:
        del messages, tools, options
        raise _ServiceUnavailable("temporarily unavailable")
        yield FinalMessage(content="", stop_reason="end_turn")


async def test_provider_exception_is_a_structured_event() -> None:
    events = await _collect_provider_events(
        _FailingProvider(),
        [],
        [],
        None,
        lambda _event: None,
    )

    assert events == [
        ProviderFailure(
            message="temporarily unavailable",
            exception_type="_ServiceUnavailable",
            status_code=503,
        )
    ]


async def test_provider_failure_reaches_loop_and_harness_results() -> None:
    result = await run_agent_loop(
        [UserMessage(content="continue")],
        AgentContext(),
        AgentLoopConfig(
            provider=_FailingProvider(),
            max_step_retries=0,
        ),
        lambda _event: None,
    )

    assert result.termination_reason is TerminationReason.PROVIDER_ERROR
    assert result.error_detail == "Provider error: temporarily unavailable"
    assert result.provider_failure is not None
    assert result.provider_failure.exception_type == "_ServiceUnavailable"
    assert result.provider_failure.status_code == 503

    harness_result = build_structured_result(result)
    assert harness_result.provider_failure == result.provider_failure


def test_structured_result_uses_only_last_assistant_as_answer() -> None:
    result = AgentLoopResult(
        messages=[
            AssistantMessage(
                content=[
                    TextContent(text="I will inspect the file."),
                    ToolCallContent(
                        id="call-1",
                        name="read_file",
                        arguments={"path": "README.md"},
                    ),
                ]
            ),
            ToolResultMessage(
                tool_call_id="call-1",
                tool_name="read_file",
                content="contents",
            ),
            AssistantMessage(content=[TextContent(text="The fix is complete.")]),
        ],
        surface=[],
        steps=2,
    )

    harness_result = build_structured_result(result)

    assert harness_result.answer == "The fix is complete."
    assert len(harness_result.tool_calls) == 1


async def test_non_retryable_provider_failure_stops_after_first_request() -> None:
    class _ForbiddenProvider:
        calls = 0

        @property
        def model(self) -> str:
            return "forbidden-model"

        async def stream(
            self,
            messages: list[dict[str, object]],
            tools: list[ToolDefinition],
            options: StreamOptions | None = None,
            **_kwargs: object,
        ) -> AsyncIterator[ProviderEvent]:
            del messages, tools, options
            self.calls += 1
            raise _Forbidden("request forbidden")
            yield FinalMessage(content="", stop_reason="end_turn")

    provider = _ForbiddenProvider()
    result = await run_agent_loop(
        [UserMessage(content="continue")],
        AgentContext(),
        AgentLoopConfig(
            provider=provider,
            max_step_retries=3,
            retry_backoff_base=0,
        ),
        lambda _event: None,
    )

    assert provider.calls == 1
    assert result.termination_reason is TerminationReason.PROVIDER_ERROR
    assert result.provider_failure is not None
    assert result.provider_failure.status_code == 403


async def test_oversized_active_turn_is_rejected_before_provider_request() -> None:
    class _RecordingProvider:
        def __init__(self) -> None:
            self.calls = 0

        async def stream(
            self,
            messages: list[dict[str, object]],
            tools: list[ToolDefinition],
            options: StreamOptions | None = None,
            **_kwargs: object,
        ) -> AsyncIterator[ProviderEvent]:
            del messages, tools, options
            self.calls += 1
            yield FinalMessage(content="unexpected", stop_reason="end_turn")

    provider = _RecordingProvider()
    assemblies: list[RequestAssembly] = []
    events: list[object] = []
    result = await run_agent_loop(
        [UserMessage(content="x" * 10_000)],
        AgentContext(),
        AgentLoopConfig(
            provider=provider,
            request_token_budget=64,
            max_step_retries=3,
            retry_backoff_base=0,
            request_rollover_decision=(
                lambda _messages, estimated: (
                    "token_limit" if (estimated or 0) > 64 else None
                )
            ),
            rollover_context=lambda messages: [messages[-1]],
            before_provider_request=assemblies.append,
        ),
        events.append,
    )

    assert provider.calls == 0
    assert result.termination_reason is TerminationReason.PROVIDER_ERROR
    assert result.provider_failure is not None
    assert result.provider_failure.exception_type == "RequestBudgetExceededError"
    assert result.provider_failure.status_code == 413
    assert "estimated" in (result.error_detail or "")
    assert result.metrics is not None
    assert result.metrics.llm_calls == 0
    assert len(assemblies) == 1
    assert assemblies[0].estimated_tokens > assemblies[0].token_budget
    assert any(isinstance(event, ContextWindowResetEvent) for event in events)


async def test_agent_loop_rolls_over_using_prepared_request_budget() -> None:
    class _FinalProvider:
        def __init__(self) -> None:
            self.requests: list[list[dict[str, object]]] = []

        async def stream(
            self,
            messages: list[dict[str, object]],
            tools: list[ToolDefinition],
            options: StreamOptions | None = None,
            **_kwargs: object,
        ) -> AsyncIterator[ProviderEvent]:
            del tools, options
            self.requests.append(messages)
            yield FinalMessage(content="done", stop_reason="end_turn")

    provider = _FinalProvider()
    context_manager = ContextManager()
    events: list[object] = []
    result = await run_agent_loop(
        [UserMessage(content="x" * 1_000)],
        AgentContext(context_manager=context_manager),
        AgentLoopConfig(
            provider=provider,
            request_rollover_decision=(
                lambda _messages, estimated: (
                    "token_limit" if (estimated or 0) >= 100 else None
                )
            ),
            rollover_context=lambda _messages: [UserMessage(content="fresh window")],
        ),
        events.append,
    )

    assert result.termination_reason is TerminationReason.COMPLETED
    assert len(provider.requests) == 1
    assert provider.requests[0] == [{"role": "user", "content": "fresh window"}]
    assert any(isinstance(event, ContextWindowResetEvent) for event in events)
    assert context_manager.prompt_cache.request_count == 1


async def test_agent_loop_preserves_legacy_rollover_hook_signature() -> None:
    class _FinalProvider:
        async def stream(
            self,
            messages: list[dict[str, object]],
            tools: list[ToolDefinition],
            options: StreamOptions | None = None,
            **_kwargs: object,
        ) -> AsyncIterator[ProviderEvent]:
            del messages, tools, options
            yield FinalMessage(content="done", stop_reason="end_turn")

    inspected: list[int] = []

    def legacy_decision(messages: list[object]) -> None:
        inspected.append(len(messages))

    await run_agent_loop(
        [UserMessage(content="hello")],
        AgentContext(),
        AgentLoopConfig(
            provider=_FinalProvider(),
            rollover_decision=legacy_decision,
            rollover_context=lambda messages: messages,
        ),
        lambda _event: None,
    )

    assert inspected == [1]
