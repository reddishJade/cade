"""Agent 辅助函数纯函数单元测试。"""

from __future__ import annotations

from cade.agent.messages import ToolResultMessage
from cade.harness.agent_runtime.agent_helpers import (
    _tool_result_status,
    text_from_blocks,
)


class TestToolResultStatus:
    def test_interrupted(self) -> None:
        msg = ToolResultMessage(
            tool_call_id="c1",
            tool_name="t",
            content="Interrupted by user",
            is_error=True,
        )
        assert _tool_result_status(msg) == "interrupted"

    def test_cancelled(self) -> None:
        msg = ToolResultMessage(
            tool_call_id="c1", tool_name="t", content="cancelled", is_error=True
        )
        assert _tool_result_status(msg) == "interrupted"


class TestTextFromBlocks:
    def test_non_text_blocks_skipped(self) -> None:
        blocks = [
            {"type": "tool_use", "name": "search"},
            {"type": "text", "text": "result"},
        ]
        assert text_from_blocks(blocks) == "result"
