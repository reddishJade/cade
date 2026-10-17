"""保护 Codex Responses Lite 请求格式与会话缓存标识。

输入样例使用固定合成消息，覆盖指令、工具调用和工具结果。
输出用量取自 cache-lite-probe.json 第 5 条成功响应：2,711 个输入 token、
2,560 个缓存 token 和 5 个输出 token。
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from cade.ai.events import ProviderEvent, UsageUpdate
from cade.ai.providers.responses import (
    OpenAICodexResponsesProvider,
    OpenAIResponsesProvider,
)
from cade.ai.types import ProviderConfig, StreamOptions, ToolDefinition

_RESPONSES_LITE_MODELS = (
    "gpt-5.6-luna",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-6-astra",
    "gpt-6-luna",
    "gpt-6-sol",
    "gpt-6.1-sol",
)
_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "answer",
        "schema": {"type": "object", "properties": {"ok": {"type": "boolean"}}},
        "strict": True,
    },
}
_EXPECTED_TEXT_FORMAT = {
    "format": {
        "type": "json_schema",
        "name": "answer",
        "schema": {"type": "object", "properties": {"ok": {"type": "boolean"}}},
        "strict": True,
    }
}
_TOOL = ToolDefinition(
    name="lookup",
    description="Look up a record.",
    parameters={
        "type": "object",
        "properties": {"query": {"type": "string"}},
    },
)
_MESSAGES = [
    {"role": "system", "content": "Follow the response schema."},
    {"role": "developer", "content": "Keep the answer concise."},
    {"role": "user", "content": "Find the first record."},
    {
        "role": "assistant",
        "tool_calls": [
            {
                "id": "call_lookup",
                "function": {"name": "lookup", "arguments": '{"query":"first"}'},
            }
        ],
    },
    {"role": "tool", "tool_call_id": "call_lookup", "content": "Record found."},
    {"role": "user", "content": "Return the result."},
]


class _ResponsesBoundary:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def create(self, **params: Any) -> list[dict[str, Any]]:
        self.requests.append(params)
        return [
            {
                "type": "response.completed",
                "response": {
                    "id": "resp_fixture",
                    "usage": {
                        "input_tokens": 2711,
                        "input_tokens_details": {
                            "cached_tokens": 2560,
                            "cache_write_tokens": 0,
                        },
                        "output_tokens": 5,
                        "output_tokens_details": {"reasoning_tokens": 0},
                    },
                },
            }
        ]


class _InjectedClient:
    def __init__(self) -> None:
        self.responses = _ResponsesBoundary()


def _config(
    model: str = "gpt-6-luna",
    *,
    thinking: bool = False,
    response_format: dict[str, Any] | None = None,
) -> ProviderConfig:
    return ProviderConfig(
        api_key="",
        model=model,
        base_url="https://chatgpt.com/backend-api",
        thinking=thinking,
        response_format=response_format,
    )


async def _collect(
    provider: OpenAICodexResponsesProvider | OpenAIResponsesProvider,
    *,
    messages: list[dict[str, Any]] | None = None,
    tools: list[ToolDefinition] | None = None,
    options: StreamOptions | None = None,
) -> list[ProviderEvent]:
    return [
        event
        async for event in provider.stream(
            messages if messages is not None else [{"role": "user", "content": "Hi."}],
            tools if tools is not None else [],
            options,
        )
    ]


@pytest.mark.asyncio
async def test_codex_lite_request_shape_and_usage() -> None:
    client = _InjectedClient()
    provider = OpenAICodexResponsesProvider(
        _config(response_format=_RESPONSE_FORMAT), client=client
    )
    events = await _collect(
        provider,
        messages=_MESSAGES,
        tools=[_TOOL],
        options=StreamOptions(
            session_id="explicit-session",
            headers={
                "SESSION-ID": "caller-header",
                "Thread-Id": "caller-header",
                "x-trace-label": "preserved",
            },
        ),
    )

    request = client.responses.requests[0]
    headers = request["extra_headers"]
    assert headers["x-openai-internal-codex-responses-lite"] == "true"
    assert headers["version"] == "0.163.0"
    assert headers["session-id"] == "explicit-session"
    assert headers["thread-id"] == "explicit-session"
    assert headers["x-trace-label"] == "preserved"
    assert sum(key.lower() == "session-id" for key in headers) == 1
    assert sum(key.lower() == "thread-id" for key in headers) == 1

    assert "instructions" not in request
    assert "tools" not in request
    assert request["parallel_tool_calls"] is False
    assert request["prompt_cache_key"] == "explicit-session"
    assert request["text"] == _EXPECTED_TEXT_FORMAT
    assert request["reasoning"]["context"] == "all_turns"
    assert request["reasoning"]["effort"] == "none"
    assert request["input"] == [
        {
            "type": "additional_tools",
            "role": "developer",
            "tools": [
                {
                    "type": "function",
                    "name": "lookup",
                    "description": "Look up a record.",
                    "parameters": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                    },
                    "strict": False,
                }
            ],
        },
        {
            "type": "message",
            "role": "developer",
            "content": [
                {
                    "type": "input_text",
                    "text": "Follow the response schema.\n\nKeep the answer concise.",
                }
            ],
        },
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "Find the first record."}],
        },
        {
            "type": "function_call",
            "call_id": "call_lookup",
            "name": "lookup",
            "arguments": '{"query":"first"}',
        },
        {
            "type": "function_call_output",
            "call_id": "call_lookup",
            "output": "Record found.",
        },
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "Return the result."}],
        },
    ]

    assert [event for event in events if isinstance(event, UsageUpdate)] == [
        UsageUpdate(input_tokens=2711, output_tokens=5)
    ]
    assert provider.usage_totals.input_tokens == 151
    assert provider.usage_totals.cache_read_tokens == 2560
    assert provider.usage_totals.output_tokens == 5
    assert provider.metrics["cached_tokens"] == 2560
    assert provider.cache_hit_rate == 0.9443


@pytest.mark.parametrize("model", _RESPONSES_LITE_MODELS)
@pytest.mark.asyncio
async def test_supported_codex_lite_models_use_lite_contract(model: str) -> None:
    client = _InjectedClient()
    provider = OpenAICodexResponsesProvider(_config(model), client=client)
    await _collect(provider)

    request = client.responses.requests[0]
    assert request["extra_headers"]["x-openai-internal-codex-responses-lite"] == "true"
    assert request["parallel_tool_calls"] is False
    assert request["input"][:2] == [
        {"type": "additional_tools", "role": "developer", "tools": []},
        {
            "type": "message",
            "role": "developer",
            "content": [{"type": "input_text", "text": ""}],
        },
    ]
    assert request["reasoning"]["context"] == "all_turns"
    assert "instructions" not in request
    assert "tools" not in request


@pytest.mark.asyncio
async def test_codex_default_session_is_stable_per_provider() -> None:
    first_client = _InjectedClient()
    second_client = _InjectedClient()
    first_provider = OpenAICodexResponsesProvider(_config(), client=first_client)
    second_provider = OpenAICodexResponsesProvider(_config(), client=second_client)

    await _collect(first_provider)
    await _collect(first_provider)
    await _collect(second_provider)

    first_requests = first_client.responses.requests
    second_requests = second_client.responses.requests
    first_key = first_requests[0]["prompt_cache_key"]
    second_provider_key = second_requests[0]["prompt_cache_key"]
    assert first_key == first_requests[1]["prompt_cache_key"]
    assert uuid.UUID(first_key)
    assert first_key != second_provider_key
    for request in (*first_requests, *second_requests):
        headers = request["extra_headers"]
        assert headers["session-id"] == request["prompt_cache_key"]
        assert headers["thread-id"] == request["prompt_cache_key"]


@pytest.mark.asyncio
async def test_codex_session_override_is_isolated_and_bounded() -> None:
    client = _InjectedClient()
    provider = OpenAICodexResponsesProvider(_config(), client=client)
    await _collect(provider, options=StreamOptions(session_id="session-one"))
    await _collect(provider, options=StreamOptions(session_id="session-two"))
    await _collect(provider, options=StreamOptions(session_id="s" * 100))

    first, second, long_session = client.responses.requests
    assert first["prompt_cache_key"] == "session-one"
    assert second["prompt_cache_key"] == "session-two"
    assert first["prompt_cache_key"] != second["prompt_cache_key"]
    assert len(long_session["prompt_cache_key"]) == 64
    for request in (first, second, long_session):
        headers = request["extra_headers"]
        assert headers["session-id"] == request["prompt_cache_key"]
        assert headers["thread-id"] == request["prompt_cache_key"]


@pytest.mark.asyncio
async def test_gpt_5_5_keeps_standard_codex_request_shape() -> None:
    client = _InjectedClient()
    provider = OpenAICodexResponsesProvider(
        _config("gpt-5.5", response_format=_RESPONSE_FORMAT), client=client
    )
    await _collect(provider, messages=_MESSAGES, tools=[_TOOL])

    request = client.responses.requests[0]
    headers = request["extra_headers"]
    assert "x-openai-internal-codex-responses-lite" not in headers
    assert headers["version"] == "0.163.0"
    assert request["parallel_tool_calls"] is True
    assert request["instructions"] == (
        "Follow the response schema.\n\nKeep the answer concise."
    )
    assert request["tools"][0]["name"] == "lookup"
    assert request["input"][0]["content"][0]["text"] == "Find the first record."
    assert request["text"] == _EXPECTED_TEXT_FORMAT
    assert request.get("reasoning", {}).get("context") is None
    assert request["prompt_cache_key"] == headers["session-id"]
    assert headers["thread-id"] == request["prompt_cache_key"]


@pytest.mark.asyncio
async def test_regular_openai_responses_request_remains_unchanged() -> None:
    client = _InjectedClient()
    provider = OpenAIResponsesProvider(
        _config("gpt-5.5", response_format=_RESPONSE_FORMAT), client=client
    )
    await _collect(
        provider,
        messages=_MESSAGES,
        tools=[_TOOL],
        options=StreamOptions(session_id="codex-session"),
    )

    request = client.responses.requests[0]
    assert request["instructions"] == (
        "Follow the response schema.\n\nKeep the answer concise."
    )
    assert request["tools"][0]["name"] == "lookup"
    assert request["text"] == _EXPECTED_TEXT_FORMAT
    assert "parallel_tool_calls" not in request
    assert "prompt_cache_key" not in request
    assert "store" not in request
    assert "extra_headers" not in request
