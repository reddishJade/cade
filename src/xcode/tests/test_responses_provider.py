"""OpenAI Responses API Provider 单元测试。"""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest
from openai import PermissionDeniedError

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
    OpenAICodexResponsesProvider,
    OpenAIResponsesProvider,
    ProviderRequestError,
    _codex_sdk_base_url,
    _safe_openai_error_detail,
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


async def test_sync_response_stream_does_not_block_incremental_delivery() -> None:
    release = threading.Event()

    class _BlockingResponseStream:
        def __init__(self) -> None:
            self._index = 0

        def __iter__(self) -> _BlockingResponseStream:
            return self

        def __next__(self) -> SimpleNamespace:
            self._index += 1
            if self._index == 1:
                return SimpleNamespace(
                    type="response.output_text.delta",
                    delta="first",
                )
            if self._index == 2:
                release.wait(timeout=1)
                return SimpleNamespace(
                    type="response.output_text.delta",
                    delta=" second",
                )
            if self._index == 3:
                return SimpleNamespace(
                    type="response.completed",
                    response=SimpleNamespace(id="resp_stream", usage=None),
                )
            raise StopIteration

    mock_client = MagicMock()
    mock_client.responses.create.return_value = _BlockingResponseStream()
    provider = OpenAIResponsesProvider(
        ProviderConfig(api_key="sk-test", model="gpt-5.5"),
        client=mock_client,
    )
    events = provider.stream([{"role": "user", "content": "Hi"}], [])

    first = await anext(events)
    assert isinstance(first, TextDelta)
    assert first.chunk == "first"

    second_task = asyncio.create_task(anext(events))
    started = time.perf_counter()
    await asyncio.sleep(0.02)
    elapsed = time.perf_counter() - started
    assert elapsed < 0.1
    assert not second_task.done()

    release.set()
    second = await asyncio.wait_for(second_task, timeout=1)
    assert isinstance(second, TextDelta)
    assert second.chunk == " second"
    await events.aclose()


def test_codex_sdk_base_url_targets_codex_responses_route() -> None:
    assert (
        _codex_sdk_base_url("https://chatgpt.com/backend-api")
        == "https://chatgpt.com/backend-api/codex"
    )


def test_safe_error_detail_accepts_json_but_rejects_html() -> None:
    assert (
        _safe_openai_error_detail(
            {"error": {"message": "Unsupported parameter: example"}}
        )
        == "Unsupported parameter: example"
    )
    assert (
        _safe_openai_error_detail({"error": {"message": "<html>blocked</html>"}})
        is None
    )
    assert _safe_openai_error_detail("<html>blocked</html>") is None
    assert (
        _codex_sdk_base_url("https://chatgpt.com/backend-api/codex/responses")
        == "https://chatgpt.com/backend-api/codex"
    )


async def test_codex_provider_uses_login_transport_contract() -> None:
    mock_client = MagicMock()
    mock_client.responses.create.return_value = iter(
        [
            SimpleNamespace(
                type="response.completed",
                response=SimpleNamespace(id="resp_codex", usage=None),
            )
        ]
    )
    provider = OpenAICodexResponsesProvider(
        ProviderConfig(
            api_key="access-token",
            model="gpt-5.6-sol",
            base_url="https://chatgpt.com/backend-api",
            reasoning_effort="high",
            extra={"account_id": "account-123"},
        ),
        client=mock_client,
    )

    events = [
        event
        async for event in provider.stream(
            messages=[{"role": "user", "content": "hello"}],
            tools=[
                ToolDefinition(
                    name="dispatch",
                    description="Dispatch one or more tasks",
                    parameters={
                        "type": "object",
                        "properties": {
                            "prompt": {"type": "string"},
                            "tasks": {"type": "array", "items": {"type": "string"}},
                        },
                        "oneOf": [
                            {"required": ["prompt"]},
                            {"required": ["tasks"]},
                        ],
                    },
                )
            ],
        )
    ]

    assert isinstance(events[-1], FinalMessage)
    params = mock_client.responses.create.call_args.kwargs
    assert params["store"] is False
    assert params["include"] == ["reasoning.encrypted_content"]
    assert params["parallel_tool_calls"] is True
    assert params["tool_choice"] == "auto"
    assert params["reasoning"] == {"effort": "high", "summary": "auto"}
    assert "stream_options" not in params
    assert "strict" not in params["tools"][0]
    assert "oneOf" in params["tools"][0]["parameters"]
    assert params["extra_headers"]["OpenAI-Beta"] == "responses=experimental"
    assert params["extra_headers"]["Accept"] == "text/event-stream"
    assert params["extra_headers"]["chatgpt-account-id"] == "account-123"


def test_codex_provider_configures_async_sdk_route_without_retries() -> None:
    provider = OpenAICodexResponsesProvider(
        ProviderConfig(
            api_key="access-token",
            model="gpt-5.6-sol",
            base_url="https://chatgpt.com/backend-api",
        )
    )

    with patch("openai.AsyncOpenAI") as client_class:
        provider._get_client_and_headers()

    kwargs = client_class.call_args.kwargs
    assert kwargs["base_url"] == "https://chatgpt.com/backend-api/codex"
    assert kwargs["max_retries"] == 0
    assert kwargs["default_headers"]["originator"] == "xcode"


async def test_cloudflare_error_is_short_and_actionable() -> None:
    request = httpx.Request("POST", "https://chatgpt.com/backend-api/codex/responses")
    response = httpx.Response(
        403,
        request=request,
        headers={"cf-mitigated": "challenge"},
    )
    error = PermissionDeniedError(
        "<html><body>challenge-error-text</body></html>",
        response=response,
        body="<html>cloudflare challenge</html>",
    )
    mock_client = MagicMock()
    mock_client.responses.create.side_effect = error
    provider = OpenAICodexResponsesProvider(
        ProviderConfig(
            api_key="access-token",
            model="gpt-5.6-sol",
            base_url="https://chatgpt.com/backend-api",
        ),
        client=mock_client,
    )

    with pytest.raises(ProviderRequestError) as exc_info:
        async for _event in provider.stream(
            messages=[{"role": "user", "content": "hello"}],
            tools=[],
        ):
            pass

    assert exc_info.value.status_code == 403
    assert "Cloudflare challenge" in str(exc_info.value)
    assert "<html>" not in str(exc_info.value)
