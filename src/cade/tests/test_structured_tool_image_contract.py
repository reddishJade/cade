"""工具图片经过执行、消息编码与 Chat/Responses 适配后仍可访问。"""

from __future__ import annotations

import pytest

from cade.agent._codec import convert_to_llm
from cade.agent._execution import _tool_result_message
from cade.agent.messages import ToolResultMessage
from cade.agent.types import ImageContent, TextContent, ToolCallContent
from cade.ai.providers._codec import to_chat_messages
from cade.ai.providers.responses import to_responses_input

IMAGE_DATA = "aGVsbG8="
IMAGE_URL = f"data:image/png;base64,{IMAGE_DATA}"


def test_image_metadata_becomes_structured_tool_message() -> None:
    tool = ToolCallContent(id="read-image", name="read", arguments={})
    result = _tool_result_message(
        tool,
        [TextContent(text="Loaded image")],
        False,
        {"image": {"mime": "image/png", "data": IMAGE_DATA}},
    )
    assert isinstance(result.content, list)
    assert isinstance(result.content[-1], ImageContent)
    assert result.content[-1].source == {
        "type": "base64",
        "media_type": "image/png",
        "data": IMAGE_DATA,
    }
    assert convert_to_llm([result])[0]["content"] == [
        {"type": "text", "text": "Loaded image"},
        {"type": "image_url", "image_url": {"url": IMAGE_URL}},
    ]


def test_chat_keeps_all_tool_outputs_before_synthetic_image_message() -> None:
    image_result = ToolResultMessage(
        tool_call_id="a",
        content=[
            TextContent(text="Screenshot"),
            ImageContent(
                source={"type": "base64", "media_type": "image/png", "data": IMAGE_DATA}
            ),
        ],
    )
    text_result = ToolResultMessage(tool_call_id="b", content="Other output")
    encoded = convert_to_llm([image_result, text_result])
    chat = to_chat_messages(encoded)
    assert [message["role"] for message in chat] == ["tool", "tool", "user"]
    assert chat[0]["content"] == "Screenshot"
    assert chat[1]["content"] == "Other output"
    assert chat[-1]["content"][1] == {
        "type": "image_url",
        "image_url": {"url": IMAGE_URL},
    }


def test_responses_function_output_preserves_image_payload() -> None:
    result = ToolResultMessage(
        tool_call_id="a",
        content=[
            TextContent(text="Screenshot"),
            ImageContent(
                source={"type": "base64", "media_type": "image/png", "data": IMAGE_DATA}
            ),
        ],
    )
    output = to_responses_input(convert_to_llm([result]))
    assert output == [
        {
            "type": "function_call_output",
            "call_id": "a",
            "output": [
                {"type": "input_text", "text": "Screenshot"},
                {"type": "input_image", "image_url": IMAGE_URL, "detail": "auto"},
            ],
        }
    ]


def test_text_tool_output_stays_a_string() -> None:
    result = ToolResultMessage(tool_call_id="t", content="text-only")
    assert convert_to_llm([result])[0]["content"] == "text-only"
    assert to_responses_input(convert_to_llm([result]))[0]["output"] == "text-only"


def test_invalid_image_fails_explicitly() -> None:
    result = ToolResultMessage(
        tool_call_id="a",
        content=[ImageContent(source={"type": "base64", "data": ""})],
    )
    with pytest.raises(ValueError, match="base64"):
        convert_to_llm([result])
