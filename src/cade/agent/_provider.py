"""Provider 交互逻辑。

从 agent_loop.py 提取的 provider 调用、事件收集和消息组装逻辑。

**提取的设计原因**：
- 关注点分离：agent_loop.py 专注于循环编排，_provider.py 专注于 LLM 交互
- 测试隔离：provider 交互逻辑可以独立测试
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from time import perf_counter
from typing import cast

from cade.agent.config import AgentContext, AgentLoopConfig
from cade.agent.events import (
    AgentEvent,
    ReasoningDeltaEvent,
    ResponseMetricsData,
    ResponseMetricsEvent,
    TextDeltaEvent,
    UsageUpdateEvent,
)
from cade.agent.messages import AssistantMessage
from cade.agent.request import RequestAssembly
from cade.agent.results import AgentLoopMetrics
from cade.agent.types import (
    CancellationSignal,
    ContentBlock,
    TextContent,
    ToolCallContent,
)
from cade.ai.events import (
    FinalMessage,
    Message,
    ProviderEvent,
    ProviderFailure,
    ReasoningDelta,
    StopReason,
    TextDelta,
    ToolCallEvent,
    UsageUpdate,
)
from cade.ai.providers.base import StreamProvider
from cade.ai.types import StreamOptions, ToolDefinition

logger = logging.getLogger(__name__)


@dataclass
class _ResponseTiming:
    """以单调时钟测量请求；TTFT 含思考，TPS 不含首增量前的等待。"""

    started: float = field(default_factory=perf_counter)
    first_token_at: float | None = None
    finished_at: float | None = None
    output_tokens: int | None = None
    succeeded: bool = False

    def observe(self, event: ProviderEvent) -> bool:
        """记录事件时间和用量，返回是否首次收到非空内容增量。"""
        first = (
            self.first_token_at is None
            and isinstance(event, (TextDelta, ReasoningDelta))
            and bool(event.chunk)
        )
        if first:
            self.first_token_at = perf_counter()
        if isinstance(event, FinalMessage):
            self.finished_at = perf_counter()
            self.succeeded = event.stop_reason not in {"error", "aborted", "cancelled"}
        elif isinstance(event, ProviderFailure):
            self.succeeded = False
        elif isinstance(event, UsageUpdate):
            self.output_tokens = (self.output_tokens or 0) + event.output_tokens
        return first

    def event(
        self, *, completed: bool, cancelled: bool = False
    ) -> ResponseMetricsEvent:
        """仅完整响应使用服务端用量；缺少用量或时间时不估算速度。"""
        duration = (
            self.finished_at - self.first_token_at
            if self.finished_at is not None and self.first_token_at is not None
            else 0.0
        )
        tps = None
        if (
            completed
            and self.succeeded
            and not cancelled
            and self.output_tokens is not None
            and self.output_tokens > 0
            and duration > 0
        ):
            tps = self.output_tokens / duration
        return ResponseMetricsEvent(
            data=ResponseMetricsData(
                ttft_seconds=(
                    self.first_token_at - self.started
                    if self.first_token_at is not None
                    else None
                ),
                tokens_per_second=tps,
                completed=completed,
            )
        )


class LlmCallLimitReached(RuntimeError):
    """模型调用预算已耗尽。"""


async def call_provider(
    context: AgentContext,
    config: AgentLoopConfig,
    emit: Callable[[AgentEvent], None],
    signal: CancellationSignal | None,
    metrics: AgentLoopMetrics,
    provider: StreamProvider,
    current_step: int = 0,
    assembly: RequestAssembly | None = None,
) -> AssistantMessage | None:
    """调用 provider；若流式生成期间被打断则返回 None。"""
    if config.max_llm_calls is not None and metrics.llm_calls >= config.max_llm_calls:
        raise LlmCallLimitReached
    context.context_manager.bind_provider(provider)
    if assembly is None:
        assembly = config.request_assembler.assemble(
            context,
            current_step=current_step,
            options=config.options,
        )
    assembly.require_sendable()
    context.context_manager.record_request(assembly)
    if config.before_provider_request:
        config.before_provider_request(assembly)

    started = perf_counter()
    metrics.llm_calls += 1
    response = await _stream_response(
        provider,
        list(assembly.wire_messages),
        list(assembly.tools),
        assembly.options,
        emit,
        signal,
        metrics,
    )
    elapsed = round((perf_counter() - started) * 1000, 3)
    metrics.model_latencies_ms.append(elapsed)
    if response is None:
        return None
    context.context_manager.record_provider_usage(
        response.usage, request_succeeded=response.stop_reason != "error"
    )
    return response


def _is_cancelled(signal: CancellationSignal | None) -> bool:
    return signal is not None and signal.is_cancelled()


def _abort_inflight_stream(provider: StreamProvider) -> None:
    """尽力中止在途 HTTP 流：关闭底层连接使阻塞读取立刻失败。"""
    abort = getattr(provider, "abort_active_stream", None)
    if not callable(abort):
        return
    try:
        abort()
    except (OSError, RuntimeError, TypeError, ValueError):
        logger.debug("failed to abort the active provider stream", exc_info=True)


async def _aclose_stream(stream_iter: AsyncIterator[ProviderEvent]) -> None:
    """尽快关闭异步流迭代器，让底层生成器的清理逻辑及时执行。"""
    aclose = getattr(stream_iter, "aclose", None)
    if not callable(aclose):
        return
    try:
        await cast(Awaitable[None], aclose())
    except (OSError, RuntimeError, TypeError, ValueError):
        logger.debug("failed to close the provider stream", exc_info=True)


async def _stream_response(
    provider: StreamProvider,
    llm_messages: list[Message],
    tool_definitions: list[ToolDefinition],
    options: StreamOptions | None,
    emit: Callable[[AgentEvent], None],
    signal: CancellationSignal | None,
    metrics: AgentLoopMetrics,
) -> AssistantMessage | None:
    """单次消费响应流，同时发布增量并构建最终消息；取消时丢弃未完成响应。"""
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls: list[ToolCallContent] = []
    stop_reason: StopReason = "end_turn"
    input_tokens = 0
    output_tokens = 0
    has_usage = False
    final_content: str | None = None
    provider_failure: ProviderFailure | None = None
    timing = _ResponseTiming()
    try:
        kwargs = {}
        if options is not None:
            kwargs["options"] = options
        stream_iter = provider.stream(llm_messages, tool_definitions, **kwargs)

        async def consume() -> None:
            nonlocal stop_reason, input_tokens, output_tokens, has_usage
            nonlocal final_content, provider_failure
            async for event in stream_iter:
                if _is_cancelled(signal):
                    _abort_inflight_stream(provider)
                    await _aclose_stream(stream_iter)
                    return
                if timing.observe(event):
                    emit(timing.event(completed=False))
                if isinstance(event, TextDelta):
                    text_parts.append(event.chunk)
                    if event.chunk:
                        emit(TextDeltaEvent(data=event.chunk))
                    await asyncio.sleep(0)
                elif isinstance(event, ReasoningDelta):
                    reasoning_parts.append(event.chunk)
                    emit(ReasoningDeltaEvent(data=event.chunk))
                    await asyncio.sleep(0)
                elif isinstance(event, ToolCallEvent):
                    tool_calls.extend(_tool_call_content_blocks(event))
                elif isinstance(event, UsageUpdate):
                    input_tokens += event.input_tokens
                    output_tokens += event.output_tokens
                    has_usage = True
                    emit(
                        UsageUpdateEvent(
                            data={
                                "input_tokens": event.input_tokens,
                                "output_tokens": event.output_tokens,
                            }
                        )
                    )
                elif isinstance(event, ProviderFailure):
                    provider_failure = event
                    stop_reason = "error"
                    final_content = f"Provider error: {event.message}"
                else:
                    stop_reason = event.stop_reason or "end_turn"
                    if event.content:
                        final_content = event.content

        # 无事件的在途请求也必须响应取消，不能依赖下一条流事件唤醒。
        pending = asyncio.create_task(consume())
        try:
            while not pending.done():
                if _is_cancelled(signal):
                    pending.cancel()
                    _abort_inflight_stream(provider)
                    await asyncio.gather(pending, return_exceptions=True)
                    await _aclose_stream(stream_iter)
                    return None
                await asyncio.wait({pending}, timeout=0.1)
            await pending
            if _is_cancelled(signal):
                return None
        finally:
            if not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
    except (
        LookupError,
        OSError,
        RuntimeError,
        TimeoutError,
        TypeError,
        ValueError,
    ) as exc:
        if _is_cancelled(signal):
            # 打断触发的连接关闭会使阻塞读取抛出异常，属预期路径。
            return None
        provider_failure = ProviderFailure(
            message=str(exc) or type(exc).__name__,
            exception_type=type(exc).__name__,
            status_code=_exception_status_code(exc),
        )
        stop_reason = "error"
        final_content = f"Provider error: {provider_failure.message}"
        timing.succeeded = False
    finally:
        emit(timing.event(completed=True, cancelled=_is_cancelled(signal)))

    if final_content and not text_parts:
        text_parts.append(final_content)
    content: list[ContentBlock] = [TextContent(text="".join(text_parts)), *tool_calls]
    usage = None
    if has_usage:
        metrics.input_tokens += input_tokens
        metrics.output_tokens += output_tokens
        usage = {
            "prompt_tokens": input_tokens,
            "completion_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        }
    return AssistantMessage(
        content=content,
        reasoning_content="".join(reasoning_parts) if reasoning_parts else None,
        stop_reason=stop_reason,
        error_message=final_content if stop_reason == "error" else None,
        provider_failure=provider_failure,
        usage=usage,
    )


def _exception_status_code(exc: BaseException) -> int | None:
    """沿显式异常链提取 SDK 状态码，避免包装后误重试永久错误。"""
    current: BaseException | None = exc
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        for attribute in ("status_code", "status", "code"):
            value = getattr(current, attribute, None)
            if isinstance(value, int) and 100 <= value <= 599:
                return value
        current = current.__cause__
    return None


def _tool_call_content_blocks(event: ToolCallEvent) -> list[ToolCallContent]:
    return [
        ToolCallContent(
            id=call.id,
            name=call.name,
            arguments=dict(call.input),
        )
        for call in event.calls
    ]
