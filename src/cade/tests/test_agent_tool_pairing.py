"""工具调用与结果的配对修复测试。"""

from __future__ import annotations

from cade.agent._tool_pairing import repair_tool_pairing
from cade.agent.messages import (
    AssistantMessage,
    ToolResultMessage,
)
from cade.agent.types import ToolCallContent


class TestRepairToolPairing:
    def test_removes_unmatched_tool_calls(self) -> None:
        msgs = [
            AssistantMessage(
                content=[
                    ToolCallContent(id="c1", name="search"),
                    ToolCallContent(id="c2", name="not_done"),
                ],
                stop_reason="tool_use",
            ),
            ToolResultMessage(tool_call_id="c1", tool_name="search", content="ok"),
        ]
        result = repair_tool_pairing(msgs)
        assert len(result) == 2
        remaining = result[0].content
        assert len(remaining) == 1
        assert remaining[0].id == "c1"

    def test_removes_unmatched_tool_results(self) -> None:
        msgs = [
            AssistantMessage(
                content=[ToolCallContent(id="c1", name="search")],
                stop_reason="tool_use",
            ),
            ToolResultMessage(tool_call_id="c2", tool_name="other", content="orphan"),
            AssistantMessage(
                content=[ToolCallContent(id="c2", name="other")],
                stop_reason="tool_use",
            ),
        ]
        result = repair_tool_pairing(msgs)
        # c1 缺少结果而被移除；c2 的结果和调用组成完整配对。
        assert len(result) == 2
        assert result[0].tool_call_id == "c2"
