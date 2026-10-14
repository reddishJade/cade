"""保护模型历史中工具调用与结果 (Tool Call / Tool Result) 的配对与自愈修复契约。

控制性回归使用真实 validate_tool_pairing 与 repair_tool_pairing。
断言对切开的工具调用即时阻断、重复 ID 拒绝，以及在断流/中止时对悬空调用和孤儿结果的平滑自愈。
"""

from __future__ import annotations

import pytest

from cade.agent._tool_pairing import repair_tool_pairing
from cade.agent.messages import AssistantMessage, ToolResultMessage, UserMessage
from cade.agent.types import TextContent, ToolCallContent
from cade.harness.session.surface import (
    InvalidSessionSurfaceError,
    validate_tool_pairing,
)


def test_validate_tool_pairing_valid_sequence() -> None:
    """验证合法顺序的工具调用和工具结果可以通过 surface 校验。"""
    messages = [
        UserMessage(content="Run tool"),
        AssistantMessage(
            content=[
                TextContent(text="Thinking..."),
                ToolCallContent(id="call_1", name="read", arguments={"path": "a.py"}),
            ]
        ),
        ToolResultMessage(
            tool_call_id="call_1", tool_name="read", content="file content"
        ),
        AssistantMessage(content=[TextContent(text="Done")]),
    ]
    # 合法顺序不抛异常
    validate_tool_pairing(messages)


def test_validate_tool_pairing_rejects_unclosed_and_orphan_calls() -> None:
    """验证存在未闭合的工具调用或孤儿结果时，validate_tool_pairing 严格抛出异常。"""
    # 1. 工具调用未被 ToolResultMessage 闭合，紧随其后出现 UserMessage
    unclosed = [
        AssistantMessage(
            content=[
                ToolCallContent(id="call_1", name="read", arguments={}),
            ]
        ),
        UserMessage(content="Interrupted without tool result"),
    ]
    with pytest.raises(
        InvalidSessionSurfaceError, match="tool results must immediately follow"
    ):
        validate_tool_pairing(unclosed)

    # 2. 出现重复的 tool_call_id
    duplicate = [
        AssistantMessage(
            content=[
                ToolCallContent(id="call_1", name="read", arguments={}),
                ToolCallContent(id="call_1", name="read", arguments={}),
            ]
        ),
    ]
    with pytest.raises(
        InvalidSessionSurfaceError, match="duplicate tool call in surface"
    ):
        validate_tool_pairing(duplicate)

    # 3. 出现未曾声明过的孤儿 ToolResultMessage
    orphan = [
        UserMessage(content="Hi"),
        ToolResultMessage(tool_call_id="call_ghost", tool_name="read", content="ghost"),
    ]
    with pytest.raises(
        InvalidSessionSurfaceError, match="orphan tool result in surface"
    ):
        validate_tool_pairing(orphan)


def test_repair_tool_pairing_recovers_broken_history() -> None:
    """验证 repair_tool_pairing 能够自愈悬空调用与孤立结果，使历史能够重新满足下游 Provider 契约。"""
    # 构造同时含有：
    # - 正常闭合的 pair (call_1)
    # - 悬空未返回结果的调用 (call_unclosed)
    # - 没有任何对应调用的孤立结果 (call_orphan)
    broken_history = [
        UserMessage(content="Start"),
        AssistantMessage(
            content=[
                TextContent(text="Step 1"),
                ToolCallContent(id="call_1", name="read", arguments={}),
                ToolCallContent(id="call_unclosed", name="write", arguments={}),
            ]
        ),
        ToolResultMessage(tool_call_id="call_1", tool_name="read", content="Result 1"),
        ToolResultMessage(
            tool_call_id="call_orphan", tool_name="echo", content="Result Orphan"
        ),
    ]

    repaired = repair_tool_pairing(broken_history)

    # 验证修复后能够通过严格的 validate_tool_pairing
    validate_tool_pairing(repaired)

    # 确认 call_1 被完整保留
    assistant_msg = next(m for m in repaired if isinstance(m, AssistantMessage))
    tool_call_ids = [
        b.id for b in assistant_msg.content if isinstance(b, ToolCallContent)
    ]
    assert tool_call_ids == ["call_1"]

    # 确认 call_unclosed 与 call_orphan 被安全剥除
    tool_results = [m for m in repaired if isinstance(m, ToolResultMessage)]
    assert len(tool_results) == 1
    assert tool_results[0].tool_call_id == "call_1"
