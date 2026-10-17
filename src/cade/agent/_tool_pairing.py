"""工具调用与结果的配对修复。"""

from __future__ import annotations

from cade.agent.messages import AgentMessage, AssistantMessage, ToolResultMessage
from cade.agent.types import ToolCallContent


def repair_tool_pairing(messages: list[AgentMessage]) -> list[AgentMessage]:
    if not messages:
        return messages

    tool_call_ids: set[str] = set()
    tool_result_ids: set[str] = set()
    for msg in messages:
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, ToolCallContent):
                    tool_call_ids.add(block.id)
        elif isinstance(msg, ToolResultMessage) and msg.tool_call_id:
            tool_result_ids.add(msg.tool_call_id)

    repaired: list[AgentMessage] = []
    for msg in messages:
        if isinstance(msg, AssistantMessage):
            filtered_content = [
                block
                for block in msg.content
                if not isinstance(block, ToolCallContent) or block.id in tool_result_ids
            ]
            if filtered_content or (not msg.content and msg.replay_payload is not None):
                replay_payload = (
                    None
                    if len(filtered_content) != len(msg.content)
                    else msg.replay_payload
                )
                repaired.append(
                    msg.model_copy(
                        update={
                            "content": filtered_content,
                            "replay_payload": replay_payload,
                        }
                    )
                )
        elif isinstance(msg, ToolResultMessage):
            if msg.tool_call_id in tool_call_ids:
                repaired.append(msg)
        else:
            repaired.append(msg)

    return repaired
