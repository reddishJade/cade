"""保护 Agent 核心循环状态机、步骤预算终止、取消中断及工具错误隔离契约。

控制性回归使用真实 run_agent_loop、DefaultRequestAssembler 与 ContextManager。
通过注入受控协议桩断言核心运行时不变量，无需真实外部网络或大模型服务。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import ClassVar

import pytest

from cade.agent import (
    AgentContext,
    AgentLoopConfig,
    AgentLoopResult,
    CancellationSignal,
    TerminationReason,
    UserMessage,
    run_agent_loop,
)
from cade.agent.messages import AgentMessage, ToolResultMessage
from cade.agent.types import (
    AgentToolResult,
    TextContent,
    ToolArguments,
    ToolExecutionMode,
    ToolUpdateCallback,
)
from cade.ai.events import (
    FinalMessage,
    Message,
    ProviderEvent,
    TextDelta,
    ToolCall,
    ToolCallEvent,
)
from cade.ai.types import StreamOptions, ToolDefinition


class SimpleCancellationSignal:
    """受控取消信号桩。"""

    def __init__(self, reason: str = "interrupted by user") -> None:
        self._reason = reason
        self._cancelled = False

    @property
    def reason(self) -> str:
        return self._reason

    def cancel(self) -> None:
        self._cancelled = True

    def is_cancelled(self) -> bool:
        return self._cancelled


class LoopingToolProvider:
    """每次调用均请求执行工具的受控 Provider。"""

    def __init__(self, tool_name: str = "echo_tool") -> None:
        self.tool_name = tool_name
        self.call_count = 0

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolDefinition],
        options: StreamOptions | None = None,
        **kwargs: object,
    ) -> AsyncIterator[ProviderEvent]:
        self.call_count += 1
        yield ToolCallEvent(
            calls=[
                ToolCall(
                    id=f"call_{self.call_count}",
                    name=self.tool_name,
                    input={"msg": f"step_{self.call_count}"},
                )
            ]
        )
        yield FinalMessage(content="", stop_reason="tool_use")


class CompletedTextProvider:
    """单次返回最终文本回复的 Provider。"""

    def __init__(self, answer: str = "Task finished successfully.") -> None:
        self.answer = answer

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolDefinition],
        options: StreamOptions | None = None,
        **kwargs: object,
    ) -> AsyncIterator[ProviderEvent]:
        yield TextDelta(chunk=self.answer)
        yield FinalMessage(content=self.answer, stop_reason="end_turn")


class EchoTool:
    """正常回显工具。"""

    name = "echo_tool"
    label = "echo_tool"
    description = "Echoes input text"
    parameters: Mapping[str, object] = {
        "type": "object",
        "properties": {"msg": {"type": "string"}},
    }
    execution_mode: ToolExecutionMode = "sequential"
    examples: ClassVar[list[dict[str, object]]] = []

    async def execute(
        self,
        tool_call_id: str,
        params: ToolArguments,
        signal: CancellationSignal | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        return AgentToolResult(
            content=[TextContent(text=f"Echoed: {params.get('msg')}")]
        )


class CrashingTool:
    """模拟在内部执行时抛出异常的高危工具。"""

    name = "crashing_tool"
    label = "crashing_tool"
    description = "Always raises an unhandled error"
    parameters: Mapping[str, object] = {"type": "object"}
    execution_mode: ToolExecutionMode = "sequential"
    examples: ClassVar[list[dict[str, object]]] = []

    async def execute(
        self,
        tool_call_id: str,
        params: ToolArguments,
        signal: CancellationSignal | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        raise RuntimeError("Disk I/O failure inside tool execution")


async def _run_loop_to_completion(
    prompts: list[AgentMessage],
    context: AgentContext,
    config: AgentLoopConfig,
    signal: CancellationSignal | None = None,
) -> AgentLoopResult:
    result = None
    async for event in run_agent_loop(prompts, context, config, signal=signal):
        if isinstance(event, AgentLoopResult):
            result = event
    assert result is not None, "Agent loop did not yield a final AgentLoopResult"
    return result


@pytest.mark.asyncio
async def test_step_limit_termination() -> None:
    """验证当模型持续调用工具时，循环在达到 max_steps 预算后强制停止并返回 STEP_LIMIT。"""
    provider = LoopingToolProvider(tool_name="echo_tool")
    tool = EchoTool()
    context = AgentContext(
        messages=[UserMessage(content="Start loop")],
        tools=[tool],
    )
    config = AgentLoopConfig(
        provider=provider,
        max_steps=3,
    )

    result = await _run_loop_to_completion([], context, config)

    assert result.termination_reason == TerminationReason.STEP_LIMIT
    assert result.steps == 3
    assert provider.call_count == 3


@pytest.mark.asyncio
async def test_user_cancellation_signal() -> None:
    """验证外部取消信号能够立即中断循环状态机并返回 CANCELLED。"""
    provider = LoopingToolProvider()
    context = AgentContext(
        messages=[UserMessage(content="Interrupt me")],
        tools=[EchoTool()],
    )
    config = AgentLoopConfig(provider=provider, max_steps=10)

    signal = SimpleCancellationSignal(reason="User clicked cancel")
    signal.cancel()

    result = await _run_loop_to_completion([], context, config, signal=signal)

    assert result.termination_reason == TerminationReason.CANCELLED
    assert result.error_detail == "User clicked cancel"


@pytest.mark.asyncio
async def test_tool_exception_isolation_and_containment() -> None:
    """验证工具内部抛出未捕获异常时，循环不崩溃，而是将其安全封装为 ToolResultMessage(is_error=True)。"""
    provider = LoopingToolProvider(tool_name="crashing_tool")
    tool = CrashingTool()
    context = AgentContext(
        messages=[UserMessage(content="Run crashing tool")],
        tools=[tool],
    )
    # 限制跑 1 步
    config = AgentLoopConfig(
        provider=provider,
        max_steps=1,
    )

    result = await _run_loop_to_completion([], context, config)

    # 循环完成 1 步后正常由 STEP_LIMIT 退出，而不是由 Python 异常崩溃
    assert result.termination_reason == TerminationReason.STEP_LIMIT
    # 验证生成的消息列表中包含 error 类型的工具结果
    tool_results = [m for m in result.surface if isinstance(m, ToolResultMessage)]
    assert len(tool_results) == 1
    assert tool_results[0].is_error
    assert "error" in str(tool_results[0].content).lower()


@pytest.mark.asyncio
async def test_clean_end_turn_completion() -> None:
    """验证模型给出最终回答 (end_turn) 时，循环正常以 COMPLETED 状态结束。"""
    provider = CompletedTextProvider(answer="All operations completed.")
    context = AgentContext(messages=[UserMessage(content="Simple question")])
    config = AgentLoopConfig(provider=provider, max_steps=5)

    result = await _run_loop_to_completion([], context, config)

    assert result.termination_reason == TerminationReason.COMPLETED
    assert result.steps == 1
    assert any("All operations completed" in str(m) for m in result.messages)
