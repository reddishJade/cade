"""Agent 消息到 LLM provider 格式的转换。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import orjson

from cade.agent.messages import (
    AgentMessage,
    AssistantMessage,
    SystemMessage,
    ToolResultMessage,
    UserMessage,
)
from cade.agent.types import (
    ContentBlock,
    ImageContent,
    TextContent,
    ThinkingContent,
    ToolCallContent,
    ToolResultContentBlock,
    tool_content_text,
)

BRANCH_SUMMARY_PREFIX = "The following is a summary of a branch that this conversation came back from:\n\n<summary>\n"
SUMMARY_SUFFIX = "\n</summary>"


def convert_to_llm(messages: list[AgentMessage]) -> list[dict[str, Any]]:
    return [_convert_one(m) for m in messages]


def _convert_one(m: AgentMessage) -> dict[str, Any]:
    if isinstance(m, SystemMessage):
        return {"role": "system", "content": str(m.content)}

    if isinstance(m, UserMessage):
        return {
            "role": "user",
            "content": _content_parts(m.content)
            if isinstance(m.content, list)
            else m.content,
        }

    if isinstance(m, AssistantMessage):
        return _convert_assistant(m)

    if isinstance(m, ToolResultMessage):
        return _convert_tool_result(m)

    return {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": BRANCH_SUMMARY_PREFIX + m.summary + SUMMARY_SUFFIX,
            }
        ],
    }


def _convert_tool_result(m: ToolResultMessage) -> dict[str, Any]:
    return {
        "role": "tool",
        "tool_call_id": m.tool_call_id,
        "content": _content_parts(m.content)
        if isinstance(m.content, list)
        and any(isinstance(b, ImageContent) for b in m.content)
        else tool_content_text(m.content),
    }


def _content_parts(content: Sequence[ToolResultContentBlock]) -> list[dict[str, Any]]:
    """将结构化内容转换为通用消息字段；二进制数据不进入文本。"""
    parts: list[dict[str, Any]] = []
    for block in content:
        if isinstance(block, ImageContent):
            source = block.source or {}
            if source.get("type") == "base64":
                if not isinstance(source.get("data"), str) or not source["data"]:
                    raise ValueError("Image content requires base64 data")
                url = f"data:{source.get('media_type', 'image/png')};base64,{source.get('data', '')}"
            else:
                url = source.get("url")
            if not isinstance(url, str) or not url:
                raise ValueError("Image content requires base64 data or a URL")
            parts.append({"type": "image_url", "image_url": {"url": url}})
        else:
            parts.append({"type": "text", "text": tool_content_text([block])})
    return parts


def _convert_block(block: ContentBlock) -> dict[str, Any] | None:
    if isinstance(block, TextContent):
        return {"type": "text", "text": block.text}
    if isinstance(block, ToolCallContent):
        return {
            "id": block.id,
            "type": "function",
            "function": {
                "name": block.name,
                "arguments": orjson.dumps(block.arguments or {}).decode(),
            },
        }
    if isinstance(block, ThinkingContent):
        return None
    return None


def _convert_assistant(m: AssistantMessage) -> dict[str, Any]:
    content_blocks: list[dict[str, Any]] = []
    tool_calls: list[dict[str, Any]] = []
    thinking_parts: list[str] = []
    for block in m.content:
        if isinstance(block, ThinkingContent):
            thinking_parts.append(block.thinking)
            continue
        converted = _convert_block(block)
        if converted is None:
            continue
        if converted.get("type") == "function":
            tool_calls.append(converted)
        else:
            content_blocks.append(converted)

    result: dict[str, Any] = {"role": "assistant"}
    if m.reasoning_content is not None:
        result["reasoning_content"] = m.reasoning_content
    elif thinking_parts:
        result["reasoning_content"] = "".join(thinking_parts)
    if content_blocks:
        result["content"] = (
            "".join(
                b.get("text", "") for b in content_blocks if b.get("type") == "text"
            )
            or None
        )
    if tool_calls:
        result["tool_calls"] = tool_calls
    return result
