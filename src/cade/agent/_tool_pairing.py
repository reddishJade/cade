"""工具调用与结果的配对修复。"""

from __future__ import annotations

from cade.agent.messages import AgentMessage, AssistantMessage, ToolResultMessage
from cade.agent.types import ToolCallContent


def repair_tool_pairing(messages: list[AgentMessage]) -> list[AgentMessage]:
    if not messages:
        return messages

    tool_call_ids: set[str] = set()
    for msg in messages:
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, ToolCallContent):
                    tool_call_ids.add(block.id)

    tool_result_ids: set[str] = set()
    for msg in messages:
        if isinstance(msg, ToolResultMessage) and msg.tool_call_id:
            tool_result_ids.add(msg.tool_call_id)

    repaired: list[AgentMessage] = []
    for msg in messages:
        if isinstance(msg, AssistantMessage):
            filtered_content = []
            for block in msg.content:
                if isinstance(block, ToolCallContent):
                    if block.id in tool_result_ids:
                        filtered_content.append(block)
                else:
                    filtered_content.append(block)
            if filtered_content:
                repaired.append(msg.model_copy(update={"content": filtered_content}))
        elif isinstance(msg, ToolResultMessage):
            if msg.tool_call_id in tool_call_ids:
                repaired.append(msg)
        else:
            repaired.append(msg)

    return repaired
