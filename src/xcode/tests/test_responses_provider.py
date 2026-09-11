"""OpenAI Responses API Provider 单元测试。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from xcode.ai.events import (
    FinalMessage,
    ReasoningDelta,
    TextDelta,
    ToolCallEvent,
    UsageUpdate,
)
from xcode.ai.providers.registry import (
    PROVIDER_REGISTRY,
    ModelProfileConfig,
    ProviderSettings,
    build_provider_bundle,
)
from xcode.ai.providers.responses import (
    OpenAIResponsesProvider,
    extract_responses_instructions,
    to_responses_input,
    to_responses_text_config,
    to_responses_tools,
)
from xcode.ai.types import (
    ProviderConfig,
    ToolDefinition,
)


def test_extract_responses_instructions() -> None:
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "developer", "content": "Keep answers short."},
        {"role": "user", "content": "Hello!"},
    ]
    instructions, remaining = extract_responses_instructions(messages)
    assert instructions is not None
    assert "You are a helpful assistant." in instructions
    assert "Keep answers short." in instructions
    assert len(remaining) == 1
    assert remaining[0]["role"] == "user"


def test_to_responses_tools() -> None:
    tools = [
        ToolDefinition(
            name="read_file",
            description="Read file contents",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        )
    ]
    formatted = to_responses_tools(tools, strict=True)
    assert len(formatted) == 1
    tool_entry = formatted[0]
    assert tool_entry["type"] == "function"
    assert tool_entry["name"] == "read_file"
    assert tool_entry["description"] == "Read file contents"
    assert tool_entry["parameters"]["additionalProperties"] is False
    assert tool_entry["strict"] is True


def test_to_responses_input_conversion() -> None:
    messages = [
        {"role": "user", "content": "What is in test.txt?"},
        {
            "role": "assistant",
            "content": "Let me check.",
            "tool_calls": [
                {
                    "id": "call_001",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": '{"path": "test.txt"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_001",
            "content": "File contents: hello world",
        },
    ]

    items = to_responses_input(messages)
    assert len(items) == 4

    # 1. User message
    assert items[0]["type"] == "message"
    assert items[0]["role"] == "user"
    assert items[0]["content"] == [
        {"type": "input_text", "text": "What is in test.txt?"}
    ]

    # 2. Tool call
    assert items[1]["type"] == "function_call"
    assert items[1]["call_id"] == "call_001"
    assert items[1]["name"] == "read_file"
    assert items[1]["arguments"] == '{"path": "test.txt"}'

    # 3. Assistant text message
    assert items[2]["type"] == "message"
    assert items[2]["role"] == "assistant"
    assert items[2]["content"] == [{"type": "output_text", "text": "Let me check."}]

    # 4. Tool call output
    assert items[3]["type"] == "function_call_output"
    assert items[3]["call_id"] == "call_001"
    assert items[3]["output"] == "File contents: hello world"


def test_to_responses_text_config() -> None:
    assert to_responses_text_config(None) is None
    assert to_responses_text_config({"type": "json_object"}) == {
        "format": {"type": "json_object"}
    }
    schema_fmt = {
        "type": "json_schema",
        "json_schema": {"name": "Result", "schema": {"type": "object"}},
    }
    assert to_responses_text_config(schema_fmt) == {
        "format": {
            "type": "json_schema",
            "name": "Result",
            "schema": {"type": "object"},
        }
    }


def test_responses_provider_registry_entry() -> None:
    assert "openai_responses" in PROVIDER_REGISTRY
    assert "openai_codex" in PROVIDER_REGISTRY

    bundle = build_provider_bundle(
        ProviderSettings(
            env_files=(),
            model_profiles={
                "main": ModelProfileConfig(
                    transport="openai_responses",
                    api_key="test-key",
                    chat_model="gpt-5.5",
                )
            },
        )
    )
    provider = bundle.llms["main"]
    assert isinstance(provider, OpenAIResponsesProvider)
    assert provider.model == "gpt-5.5"


async def test_responses_provider_stream_events() -> None:
    # 构造模拟事件流
    mock_events = [
        # 推理输出事件
        SimpleNamespace(
            type="response.reasoning.delta",
            delta="Thinking about it...",
        ),
        # 文本输出事件
        SimpleNamespace(
            type="response.output_text.delta",
            delta="Hello, ",
        ),
        SimpleNamespace(
            type="response.output_text.delta",
            delta="world!",
        ),
        # 工具调用添加
        SimpleNamespace(
            type="response.output_item.added",
            item=SimpleNamespace(
                type="function_call",
                id="call_item_1",
                call_id="call_abc",
                name="search",
            ),
        ),
        # 工具参数增量
        SimpleNamespace(
            type="response.function_call_arguments.delta",
            item_id="call_item_1",
            delta='{"query":',
        ),
        SimpleNamespace(
            type="response.function_call_arguments.delta",
            item_id="call_item_1",
            delta=' "python"}',
        ),
        SimpleNamespace(
            type="response.function_call_arguments.done",
            item_id="call_item_1",
            arguments='{"query": "python"}',
        ),
        # 完成事件
        SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(
                id="resp_xyz123",
                usage=SimpleNamespace(
                    input_tokens=15,
                    output_tokens=25,
                    total_tokens=40,
                ),
            ),
        ),
    ]

    mock_client = MagicMock()
    mock_client.responses.create.return_value = iter(mock_events)

    provider = OpenAIResponsesProvider(
        config=ProviderConfig(
            api_key="sk-test",
            model="gpt-5.5",
            base_url="https://api.openai.com/v1",
        ),
        client=mock_client,
    )

    collected_events = [
        event
        async for event in provider.stream(
            messages=[{"role": "user", "content": "Hi"}],
            tools=[],
        )
    ]

    # 验证事件生成
    reasoning_events = [e for e in collected_events if isinstance(e, ReasoningDelta)]
    assert len(reasoning_events) == 1
    assert reasoning_events[0].chunk == "Thinking about it..."

    text_events = [e for e in collected_events if isinstance(e, TextDelta)]
    assert len(text_events) == 2
    assert "".join(e.chunk for e in text_events) == "Hello, world!"

    tool_events = [e for e in collected_events if isinstance(e, ToolCallEvent)]
    assert len(tool_events) == 1
    assert len(tool_events[0].calls) == 1
    assert tool_events[0].calls[0].name == "search"
    assert tool_events[0].calls[0].id == "call_abc"
    assert tool_events[0].calls[0].input == {"query": "python"}

    usage_events = [e for e in collected_events if isinstance(e, UsageUpdate)]
    assert len(usage_events) == 1
    assert usage_events[0].input_tokens == 15
    assert usage_events[0].output_tokens == 25

    final_events = [e for e in collected_events if isinstance(e, FinalMessage)]
    assert len(final_events) == 1
    assert final_events[0].content == "Hello, world!"
    assert final_events[0].stop_reason == "tool_use"

    # 验证 stateful session response id 跟踪
    assert provider._last_response_id == "resp_xyz123"
