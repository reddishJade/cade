"""Agent 核心循环。

Cade 的类型化 Agent 循环。模块本身不持有运行状态。

**循环设计原因**：
- 外层循环（步骤限制）：防止单次 run 无限执行
- 内层循环（换窗 → 模型调用 → 错误重试 → max_tokens 续写）：应对上下文溢出和部分生成
- 可注入设计：所有外部依赖（provider、工具、hooks）通过参数注入，便于测试和替换

流程：
  外层循环：步骤限制 + follow-up 队列驱动
  内层循环：换窗 → 模型调用 → 错误重试 → max_tokens 续写
    → stream_assistant_response()
    → execute_tool_calls()（按 execution_mode 串行/并行）
    → watchdog 检查 → should_stop_after_turn
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Callable

from cade.agent.types import CancellationSignal, TextContent, ToolCallContent
from cade.ai.providers.base import StreamProvider

from ._context_window import context_window_id, estimate_tokens
from ._execution import (
    ExecutedToolBatch,
    cancel_reason,
    execute_tool_calls,
    is_cancelled,
    update_idle_tool_watchdog,
    update_repeated_tool_watchdog,
)
from ._provider import LlmCallLimitReached, call_provider
from .config import (
    AgentContext,
    AgentLoopConfig,
    ContextWindowResetReason,
    LoopRunState,
    ShouldStopAfterTurnContext,
)
from .context import ContextAssemblyError
from .events import (
    AgentEvent,
    ContextWindowResetEvent,
    MessageEndEvent,
    MessageStartEvent,
    TurnEndEvent,
    TurnStartEvent,
)
from .messages import AgentMessage, AssistantMessage, ToolResultMessage, UserMessage
from .request import RequestAssembly
from .results import AgentLoopMetrics, AgentLoopResult, TerminationReason

# ── 公共 API ──


async def run_agent_loop(
    prompts: list[AgentMessage],
    context: AgentContext,
    config: AgentLoopConfig,
    signal: CancellationSignal | None = None,
    steer_queue: Callable[[], list[AgentMessage]] | None = None,
    finish_steering: Callable[[], list[AgentMessage]] | None = None,
    reopen_steering: Callable[[], None] | None = None,
) -> AsyncIterator[AgentEvent | AgentLoopResult]:
    """实时交付循环事件，最后交付结果；关闭消费方时清理执行任务。"""
    events: asyncio.Queue[AgentEvent | None] = asyncio.Queue()

    def emit(event: AgentEvent) -> None:
        if config.on_event is not None:
            config.on_event(event)
        events.put_nowait(event)

    async def run() -> AgentLoopResult:
        try:
            return await _run_agent_loop(
                prompts,
                context,
                config,
                emit,
                signal,
                steer_queue,
                finish_steering,
                reopen_steering,
            )
        finally:
            events.put_nowait(None)

    task = asyncio.create_task(run())
    try:
        while (event := await events.get()) is not None:
            yield event
        yield await task
    finally:
        if not task.done():
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def _run_agent_loop(
    prompts: list[AgentMessage],
    context: AgentContext,
    config: AgentLoopConfig,
    emit: Callable[[AgentEvent], None],
    signal: CancellationSignal | None = None,
    steer_queue: Callable[[], list[AgentMessage]] | None = None,
    finish_steering: Callable[[], list[AgentMessage]] | None = None,
    reopen_steering: Callable[[], None] | None = None,
) -> AgentLoopResult:
    """运行 agent 核心循环。

    将 prompt 消息合并到 context 中，执行完整的 agent 循环，
    返回 AgentLoopResult 包含所有消息、步数和指标。
    """
    new_messages: list[AgentMessage] = list(prompts)
    current_context = AgentContext(
        system_prompt=context.system_prompt,
        request_prefix=list(context.request_prefix),
        messages=list(context.messages) + list(prompts),
        tools=list(context.tools) if context.tools else [],
        context_manager=context.context_manager,
        state=dict(context.state),
        project_root=context.project_root,
        cwd=context.cwd,
        request_token_budget=(
            config.request_token_budget
            if config.request_token_budget > 0
            else context.request_token_budget
        ),
        context_policy=config.context_policy or context.context_policy,
    )
    emit(TurnStartEvent())
    for prompt in prompts:
        emit(MessageStartEvent(message=prompt))
        emit(MessageEndEvent(message=prompt))
    result = await _run_loop(
        current_context,
        new_messages,
        config,
        emit,
        signal,
        steer_queue=steer_queue,
        finish_steering=finish_steering,
        reopen_steering=reopen_steering,
    )
    current_context.context_manager.replace_history(result.surface)
    return result


# ── 外层循环 ──


async def _run_loop(
    initial_context: AgentContext,
    new_messages: list[AgentMessage],
    config: AgentLoopConfig,
    emit: Callable[[AgentEvent], None],
    signal: CancellationSignal | None = None,
    steer_queue: Callable[[], list[AgentMessage]] | None = None,
    finish_steering: Callable[[], list[AgentMessage]] | None = None,
    reopen_steering: Callable[[], None] | None = None,
) -> AgentLoopResult:
    """核心 agent 循环。

    外层：持续运行（可选步骤限制）
    内层：换窗 → 模型调用 → 重试 → max_tokens → 工具执行 → watchdog
    """
    metrics = AgentLoopMetrics()
    current_context = initial_context
    state = LoopRunState(active_provider=config.provider)

    step = 0
    while config.max_steps is None or step < config.max_steps:
        step += 1
        metrics.steps = step

        # ── 取消检查 ──
        if is_cancelled(signal):
            return _finish_loop(
                new_messages,
                current_context.messages,
                step,
                metrics,
                state.active_provider,
                termination_reason=TerminationReason.CANCELLED,
                error_detail=cancel_reason(signal),
            )

        # ── 处理 steer 队列 ──
        _append_steering_messages(current_context, new_messages, steer_queue)

        # ── 发出 turn 事件 ──
        if not state.first_turn:
            emit(TurnStartEvent())
        else:
            state.first_turn = False

        # ── 上下文窗口切换检查 ──
        if state.active_provider is not None:
            current_context.context_manager.bind_provider(state.active_provider)
        if (
            current_context.context_policy is not None
            and state.active_provider is not None
        ):
            current_context.context_policy = (
                current_context.context_policy.for_provider(state.active_provider)
            )
        # ── 内层循环：组装/换窗 + 模型调用 + 重试 + max_tokens ──
        try:
            prepared_assembly = _prepare_step_assembly(
                current_context,
                config,
                new_messages,
                steer_queue,
                emit,
                metrics,
                state.active_provider,
                step,
            )
            ctx_len_before = len(current_context.messages)
            inner_result = await _run_inner_loop(
                current_context,
                config,
                emit,
                signal,
                metrics,
                step,
                state,
                prepared_assembly,
            )
        except ContextAssemblyError as exc:
            return _finish_loop(
                new_messages,
                current_context.messages,
                step,
                metrics,
                state.active_provider,
                termination_reason=TerminationReason.CONTEXT_ERROR,
                error_detail=str(exc),
            )
        except LlmCallLimitReached:
            return _finish_loop(
                new_messages,
                current_context.messages,
                step,
                metrics,
                state.active_provider,
                termination_reason=TerminationReason.LLM_CALL_LIMIT,
            )

        if inner_result is None:
            return _finish_loop(
                new_messages,
                current_context.messages,
                step,
                metrics,
                state.active_provider,
                termination_reason=TerminationReason.PROVIDER_ERROR,
            )

        message, stop_reason, new_provider = inner_result
        state.active_provider = new_provider

        new_messages.extend(current_context.messages[ctx_len_before:-1])

        new_messages.append(message)

        # ── 错误/中止 → 退出 ──
        if stop_reason in ("error", "aborted"):
            emit(TurnEndEvent(message=message, tool_results=[]))
            result = AgentLoopResult(
                messages=new_messages,
                surface=list(current_context.messages),
                steps=step,
                termination_reason=(
                    TerminationReason.PROVIDER_ERROR
                    if stop_reason == "error"
                    else TerminationReason.CANCELLED
                ),
                error_detail=_assistant_error_detail(message),
                provider_failure=message.provider_failure,
                metrics=metrics,
                active_provider=state.active_provider,
            )
            return result

        # ── 提取工具调用 ──
        tool_calls = [b for b in message.content if isinstance(b, ToolCallContent)]

        if not tool_calls:
            # 模型没有请求工具 → 本轮结束
            emit(TurnEndEvent(message=message, tool_results=[]))

            if config.completion_verifier is not None:
                feedback = await config.completion_verifier(current_context.messages)
                if feedback:
                    _append_messages(
                        current_context,
                        new_messages,
                        [UserMessage(content=feedback)],
                    )
                    continue

            # 原子关闭入口并检查生成期间到达的末轮 steer。若存在，
            # 重新开放入口并让下一次模型调用消费这些消息。
            if finish_steering is not None and (
                config.max_steps is None or step < config.max_steps
            ):
                final_steers = finish_steering()
                if final_steers:
                    _append_messages(current_context, new_messages, final_steers)
                    if reopen_steering is not None:
                        reopen_steering()
                    continue

            return _finish_loop(
                new_messages,
                current_context.messages,
                step,
                metrics,
                state.active_provider,
            )

        # ── 工具执行 ──
        tool_started_at = time.perf_counter()
        executed: ExecutedToolBatch = await execute_tool_calls(
            current_context, message, tool_calls, config, signal, emit
        )
        metrics.tool_latencies_ms.append((time.perf_counter() - tool_started_at) * 1000)
        tool_results = executed.results
        _append_tool_results(current_context, new_messages, metrics, tool_results)

        emit(TurnEndEvent(message=message, tool_results=tool_results or []))

        # ── 重复工具看门狗 ──
        repeated_watchdog_reason = update_repeated_tool_watchdog(
            state, tool_calls, config, tool_results
        )
        if repeated_watchdog_reason:
            return _finish_loop(
                new_messages,
                current_context.messages,
                step,
                metrics,
                state.active_provider,
                termination_reason=TerminationReason.WATCHDOG,
                watchdog_reason=repeated_watchdog_reason,
            )

        # ── 空闲步骤看门狗 ──
        idle_watchdog_reason = update_idle_tool_watchdog(
            state, tool_calls, tool_results, config
        )
        if idle_watchdog_reason:
            return _finish_loop(
                new_messages,
                current_context.messages,
                step,
                metrics,
                state.active_provider,
                termination_reason=TerminationReason.WATCHDOG,
                watchdog_reason=idle_watchdog_reason,
            )

        # ── should_stop_after_turn 钩子 ──
        if config.should_stop_after_turn:
            ctx = ShouldStopAfterTurnContext(
                message=message,
                tool_results=tool_results,
                context=current_context,
                new_messages=new_messages,
            )
            if config.should_stop_after_turn(ctx):
                return _finish_loop(
                    new_messages,
                    current_context.messages,
                    step,
                    metrics,
                    state.active_provider,
                )

        # 工具已执行，继续内层循环（下一轮工具调用）
        if not executed.terminate:
            continue

    # 步骤耗尽
    result = AgentLoopResult(
        messages=new_messages,
        surface=list(current_context.messages),
        steps=step,
        termination_reason=TerminationReason.STEP_LIMIT,
        metrics=metrics,
        active_provider=state.active_provider,
    )
    return result


def _prepare_step_assembly(
    context: AgentContext,
    config: AgentLoopConfig,
    new_messages: list[AgentMessage],
    steer_queue: Callable[[], list[AgentMessage]] | None,
    emit: Callable[[AgentEvent], None],
    metrics: AgentLoopMetrics,
    provider: StreamProvider | None,
    step: int,
) -> RequestAssembly | None:
    """检查完整请求投影，再决定是否换窗；失败投影不直接发给模型。"""
    if not config.rollover_context or not (
        config.request_rollover_decision
        or config.rollover_decision
        or context.context_policy is not None
    ):
        return None
    assembly = None
    if provider is not None and (
        config.request_rollover_decision is not None
        or context.context_policy is not None
    ):
        assembly = config.request_assembler.assemble(
            context, current_step=step, options=config.options
        )
        assembly = _prepare_request_context(
            context, config, new_messages, steer_queue, assembly, step
        )
    rotation_input_tokens = (
        max(assembly.estimated_tokens, assembly.unreclaimed_input_tokens)
        if assembly is not None
        else None
    )
    if config.request_rollover_decision is not None:
        reason = config.request_rollover_decision(
            context.messages, rotation_input_tokens
        )
    elif config.rollover_decision is not None:
        reason = config.rollover_decision(context.messages)
    else:
        reason = None
    if (
        assembly is not None
        and assembly.context_policy is not None
        and rotation_input_tokens is not None
        and assembly.context_policy.should_rotate(rotation_input_tokens)
    ):
        reason = reason or "token_limit"
    if (
        reason == "token_limit"
        and assembly is not None
        and assembly.rotation_blocked_reason is not None
    ):
        reason = None
    if reason is not None:
        _rotate_context(context, config, emit, metrics, provider, reason)
        if provider is not None:
            assembly = config.request_assembler.assemble(
                context, current_step=step, options=config.options
            )
    return assembly


def _prepare_request_context(
    context: AgentContext,
    config: AgentLoopConfig,
    new_messages: list[AgentMessage],
    steer_queue: Callable[[], list[AgentMessage]] | None,
    assembly: RequestAssembly,
    step: int,
) -> RequestAssembly:
    """把按当前预算生成的运行时输入交付到同一次请求。"""
    if config.prepare_request_context is None:
        return assembly
    if not config.prepare_request_context(assembly.estimated_tokens):
        return assembly
    _append_steering_messages(context, new_messages, steer_queue)
    return config.request_assembler.assemble(
        context, current_step=step, options=config.options
    )


def _refresh_request_prefix(context: AgentContext, config: AgentLoopConfig) -> None:
    """新窗口重新采集运行时前缀，避免复用已消费的恢复通知。"""
    if config.refresh_request_prefix is not None:
        context.request_prefix = list(config.refresh_request_prefix())


def _finish_loop(
    new_messages: list[AgentMessage],
    surface: list[AgentMessage],
    step: int,
    metrics: AgentLoopMetrics,
    active_provider: StreamProvider | None,
    *,
    termination_reason: TerminationReason = TerminationReason.COMPLETED,
    watchdog_reason: str | None = None,
    error_detail: str | None = None,
) -> AgentLoopResult:
    result = AgentLoopResult(
        messages=new_messages,
        surface=list(surface),
        steps=step,
        termination_reason=termination_reason,
        watchdog_reason=watchdog_reason,
        error_detail=error_detail,
        metrics=metrics,
        active_provider=active_provider,
    )
    return result


def _reset_provider_conversation_state(provider: StreamProvider | None) -> None:
    """换窗后立即断开 provider 侧的连续会话链。"""
    reset = getattr(provider, "reset_conversation_state", None)
    if callable(reset):
        reset()


def _append_steering_messages(
    current_context: AgentContext,
    new_messages: list[AgentMessage],
    steer_queue: Callable[[], list[AgentMessage]] | None,
) -> None:
    if steer_queue is None:
        return
    msgs = steer_queue()
    if not msgs:
        return
    _append_messages(current_context, new_messages, msgs)


def _append_messages(
    current_context: AgentContext,
    new_messages: list[AgentMessage],
    messages: list[AgentMessage],
) -> None:
    """按顺序把运行时消息同时写入上下文和本轮 transcript。"""
    for msg in messages:
        current_context.messages.append(msg)
        new_messages.append(msg)


def _append_tool_results(
    current_context: AgentContext,
    new_messages: list[AgentMessage],
    metrics: AgentLoopMetrics,
    tool_results: list[ToolResultMessage],
) -> None:
    for tr in tool_results:
        current_context.messages.append(tr)
        new_messages.append(tr)
        metrics.tool_calls += 1


def _rotate_context(
    context: AgentContext,
    config: AgentLoopConfig,
    emit: Callable[[AgentEvent], None],
    metrics: AgentLoopMetrics,
    provider: StreamProvider | None,
    reason: ContextWindowResetReason,
) -> None:
    """正常换窗和超限恢复共用同一套持久化、前缀刷新和事件流程。"""
    if config.rollover_context is None:
        raise RuntimeError("context rollover is not configured")
    before = len(context.messages)
    next_window = config.rollover_context(context.messages)
    context.messages = context.context_manager.complete_rollover(
        next_window, reason=reason
    )
    _refresh_request_prefix(context, config)
    _reset_provider_conversation_state(provider)
    metrics.context_window_resets += 1
    emit(
        ContextWindowResetEvent(
            window_id=context_window_id(context.messages),
            messages_removed=max(before - len(context.messages), 0),
            messages_after=len(context.messages),
            trigger=reason,
            replacement=list(context.messages),
            reproject_from_facts=config.reproject_rollover_from_facts,
            rollover_policy=context.context_policy,
        )
    )


def _is_context_overflow(message: AssistantMessage) -> bool:
    """仅识别明确的上下文超限；普通 400/413 不触发换窗。"""
    failure = message.provider_failure
    if failure is None:
        return False
    detail = f"{failure.exception_type} {failure.message}".lower()
    return any(
        marker in detail
        for marker in (
            "context_length_exceeded",
            "context_window_exceeded",
            "maximum context length",
            "exceeds the context window",
            "exceed the context window",
            "context window exceeded",
            "context window is full",
            "input is too long for",
        )
    )


# ── 内层循环 ──


async def _run_inner_loop(
    context: AgentContext,
    config: AgentLoopConfig,
    emit: Callable[[AgentEvent], None],
    signal: CancellationSignal | None,
    metrics: AgentLoopMetrics,
    step: int,
    state: LoopRunState,
    prepared_assembly: RequestAssembly | None = None,
) -> tuple[AssistantMessage, str, StreamProvider | None] | None:
    """内层循环：模型调用 → 错误重试 → max_tokens 续写。

    通过 state 对象共享 step_retries / consecutive_continuations 计数器，
    无需外循环再手动同步。
    返回 (message, stop_reason, provider)，或 None 表示应提前退出。
    """
    provider = state.active_provider
    overflow_recovered = False
    while True:
        if is_cancelled(signal):
            return _cancelled_message(signal), "aborted", provider

        if provider is None:
            msg = AssistantMessage(
                content=[TextContent(text="No provider configured")],
                stop_reason="end_turn",
            )
            emit(MessageStartEvent(message=msg))
            emit(MessageEndEvent(message=msg))
            return msg, "end_turn", provider

        response = await call_provider(
            context,
            config,
            emit,
            signal,
            metrics,
            provider,
            current_step=step,
            assembly=prepared_assembly,
        )
        if response is None:
            # 模型流式生成期间被打断：中止在途请求并退出本轮。
            return _cancelled_message(signal), "aborted", provider
        message = response.message
        stop_reason = response.stop_reason

        # ── 检查是否为 FinalMessage 的错误 ──
        if stop_reason == "error":
            if (
                _is_context_overflow(message)
                and not overflow_recovered
                and config.recover_context_overflow
                and (
                    context.context_policy is None
                    or context.context_policy.automatic_rollover
                )
                and config.rollover_context is not None
            ):
                overflow_recovered = True
                emit(MessageEndEvent(message=message))
                _rotate_context(context, config, emit, metrics, provider, "token_limit")
                prepared_assembly = None
                continue
            if _is_context_overflow(message):
                emit(MessageEndEvent(message=message))
                return message, stop_reason, provider
            state.step_retries += 1
            should_retry, fallback_message = await _handle_provider_error(
                message, state.step_retries, config, emit
            )
            if should_retry:
                prepared_assembly = None
                continue
            if fallback_message is not None:
                context.messages.append(fallback_message)
                return fallback_message, "error", provider
            emit(MessageEndEvent(message=message))
            return message, stop_reason, provider

        # ── max_tokens 续写 ──
        if _should_continue_max_tokens(stop_reason, config):
            continuation_count = _update_continuation_count(
                message, state.consecutive_continuations, config
            )
            if continuation_count is None:
                fallback = _continuation_limit_message(config)
                context.messages.append(fallback)
                emit(MessageStartEvent(message=fallback))
                emit(MessageEndEvent(message=fallback))
                return fallback, "error", provider
            state.consecutive_continuations = continuation_count
            _append_continuation_prompt(context, message)
            prepared_assembly = None
            continue

        # ── 正常结束 ──
        context.messages.append(message)
        emit(MessageEndEvent(message=message))
        state.step_retries = 0
        state.consecutive_continuations = 0
        return message, stop_reason, provider


async def _handle_provider_error(
    message: AssistantMessage,
    step_retries: int,
    config: AgentLoopConfig,
    emit: Callable[[AgentEvent], None],
) -> tuple[bool, AssistantMessage | None]:
    if _is_non_retryable_provider_failure(message):
        return False, None
    if step_retries <= config.max_step_retries:
        delay = config.retry_backoff_base * (2 ** (step_retries - 1))
        await asyncio.sleep(delay)
        return True, None
    if config.max_step_retries > 0 or _has_empty_text_response(message):
        msg = AssistantMessage(
            content=[TextContent(text="I encountered an error.")],
            stop_reason="error",
            error_message=_assistant_error_detail(message),
            provider_failure=message.provider_failure,
        )
        emit(MessageStartEvent(message=msg))
        emit(MessageEndEvent(message=msg))
        return False, msg
    return False, None


def _is_non_retryable_provider_failure(message: AssistantMessage) -> bool:
    """鉴权、权限和请求格式错误不会因立即重试而恢复。"""
    failure = message.provider_failure
    return failure is not None and failure.status_code in {
        400,
        401,
        403,
        404,
        405,
        413,
        422,
    }


def _has_empty_text_response(message: AssistantMessage) -> bool:
    content_blocks = message.content
    return not content_blocks or (
        len(content_blocks) == 1
        and isinstance(content_blocks[0], TextContent)
        and not content_blocks[0].text
    )


def _should_continue_max_tokens(
    stop_reason: str,
    config: AgentLoopConfig,
) -> bool:
    return stop_reason == "max_tokens" and config.max_tokens_continuation


def _update_continuation_count(
    message: AssistantMessage,
    consecutive_continuations: int,
    config: AgentLoopConfig,
) -> int | None:
    """检测模型是否陷入低产出续写循环。

    续写机制设计：
    - 模型输出因 max_tokens 截断时自动续写
    - 若续写内容少于阈值（默认 500 tokens），计入低产出计数
    - 连续多次低产出（默认 3 次）则终止，防止无限循环
    """
    estimated_tokens = estimate_tokens(
        json.dumps(message.content, ensure_ascii=False, default=str)
    )
    updated = (
        consecutive_continuations + 1
        if estimated_tokens < config.min_continuation_tokens
        else 0
    )
    if updated >= config.max_consecutive_continuations:
        return None
    return updated


def _continuation_limit_message(config: AgentLoopConfig) -> AssistantMessage:
    """构造续写保护触发后的结构化错误消息。"""
    return AssistantMessage(
        content=[
            TextContent(
                text=(
                    "Diminishing Returns: consecutive output token increments "
                    f"below {config.min_continuation_tokens} limit."
                )
            )
        ],
        stop_reason="error",
    )


def _append_continuation_prompt(
    context: AgentContext,
    message: AssistantMessage,
) -> None:
    context.messages.append(message)
    context.messages.append(UserMessage(content="continue"))


def _cancelled_message(signal: CancellationSignal | None) -> AssistantMessage:
    return AssistantMessage(
        content=[],
        stop_reason="aborted",
        error_message=cancel_reason(signal),
    )


def _assistant_error_detail(message: AssistantMessage) -> str | None:
    """提取 provider 或取消返回的具体错误信息。"""
    if message.error_message:
        return message.error_message
    text = " ".join(
        block.text for block in message.content if isinstance(block, TextContent)
    ).strip()
    return text or None
