"""Agent 消息到 LLM 格式转换单元测试。"""

from __future__ import annotations

from cade.agent._codec import convert_to_llm
from cade.agent.messages import (
    AssistantMessage,
    ToolResultMessage,
)
from cade.agent.types import TextContent, ToolCallContent, ToolResultContent


class TestConvertAssistantMessage:
    def test_tool_calls(self) -> None:
        msg = AssistantMessage(
            content=[
                TextContent(text="Let me search."),
                ToolCallContent(id="c1", name="search", arguments={"q": "x"}),
            ],
            stop_reason="tool_use",
        )
        result = convert_to_llm([msg])
        assert result[0]["content"] == "Let me search."
        assert len(result[0]["tool_calls"]) == 1
        assert result[0]["tool_calls"][0]["function"]["name"] == "search"

    def test_reasoning_content_passed_through(self) -> None:
        msg = AssistantMessage(
            content=[TextContent(text="Answer")],
            reasoning_content="I think...",
            stop_reason="end_turn",
        )
        result = convert_to_llm([msg])
        assert result[0]["reasoning_content"] == "I think..."


class TestConvertToolResultMessage:
    def test_str_content(self) -> None:
        msg = ToolResultMessage(
            tool_call_id="c1",
            tool_name="search",
            content='{"result": "ok"}',
            is_error=False,
        )
        result = convert_to_llm([msg])
        assert result[0]["role"] == "tool"
        assert result[0]["tool_call_id"] == "c1"
        assert result[0]["content"] == '{"result": "ok"}'

    def test_list_content_flattened(self) -> None:
        msg = ToolResultMessage(
            tool_call_id="c1",
            tool_name="bash",
            content=[
                TextContent(text="line1"),
                TextContent(text="line2"),
                ToolResultContent(tool_use_id="", content="block"),
            ],
            is_error=False,
        )
        result = convert_to_llm([msg])
        assert result[0]["role"] == "tool"
        assert "line1" in result[0]["content"]
        assert "line2" in result[0]["content"]
        assert "block" in result[0]["content"]
