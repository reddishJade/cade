"""耗时的审批审查不得阻塞 agent 事件循环。"""

from __future__ import annotations

import asyncio
import time

from cade.agent._execution import _run_before_tool_hook
from cade.agent.config import AgentContext, AgentLoopConfig, BeforeToolCallResult
from cade.agent.messages import AssistantMessage
from cade.agent.types import ToolCallContent
from cade.harness.agent_runtime.cancellation import CancellationToken


async def test_synchronous_approval_runs_off_event_loop() -> None:
    def review(_ctx, _signal):
        time.sleep(0.15)

    tool = ToolCallContent(id="t1", name="bash", arguments={"command": "pwd"})
    pending = asyncio.create_task(
        _run_before_tool_hook(
            AgentContext(),
            AssistantMessage(content=[tool]),
            tool,
            {"command": "pwd"},
            AgentLoopConfig(before_tool_call=review),
            None,
        )
    )
    # 同步钩子会阻止这个独立协程运行。
    await asyncio.wait_for(asyncio.sleep(0.02), timeout=0.1)
    assert not pending.done()
    assert await pending is None


async def test_cancelled_review_cannot_authorize_tool_execution() -> None:
    def review(_ctx, _signal):
        time.sleep(0.15)
        return BeforeToolCallResult()

    signal = CancellationToken()
    tool = ToolCallContent(id="t2", name="bash", arguments={"command": "pwd"})
    pending = asyncio.create_task(
        _run_before_tool_hook(
            AgentContext(),
            AssistantMessage(content=[tool]),
            tool,
            {"command": "pwd"},
            AgentLoopConfig(before_tool_call=review),
            signal,
        )
    )
    await asyncio.sleep(0.02)
    signal.cancel("interrupted by user")
    result = await asyncio.wait_for(pending, timeout=0.1)
    assert result is not None and result.block
    assert "interrupted" in result.reason
