"""OpenAI Responses API provider。

支持 OpenAI /v1/responses 与 ChatGPT 后端 API，
支持 Stateful 对话 (previous_response_id)、扁平 Tool 定义及结构化输出。
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from collections.abc import AsyncIterator, Iterator
from typing import Any

from xcode.ai.cache import CacheUsage
from xcode.ai.events import (
    FinalMessage,
    ProviderEvent,
    ReasoningDelta,
    TextDelta,
    ToolCall,
    ToolCallEvent,
    UsageUpdate,
)
from xcode.ai.types import (
    ProviderConfig,
    StreamOptions,
    ToolDefinition,
)
from xcode.ai.usage import UsageAccumulator, UsageTotals

from ._codec import make_schema_strict
from ._runtime import ProviderRuntime
from ._stream import parse_tool_arguments

_LOGGER = logging.getLogger(__name__)

OPENAI_RESPONSES_DEFAULT_BASE_URL = "https://api.openai.com/v1"
CHATGPT_BACKEND_BASE_URL = "https://chatgpt.com/backend-api"


# ── 消息转换工具 ──


def extract_responses_instructions(
    messages: list[dict[str, Any]],
) -> tuple[str | None, list[dict[str, Any]]]:
    """将 system 与 developer 消息提取为 Responses API 的 instructions 参数。"""
    instructions: list[str] = []
    remaining: list[dict[str, Any]] = []

    for msg in messages:
        role = str(msg.get("role", "user"))
        content = msg.get("content")
        if role in {"system", "developer"}:
            if isinstance(content, str) and content.strip():
                instructions.append(content.strip())
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") in {
                        "text",
                        "input_text",
                    }:
                        txt = part.get("text", "")
                        if txt:
                            instructions.append(txt.strip())
        else:
            remaining.append(msg)

    combined = "\n\n".join(instructions) if instructions else None
    return combined, remaining


def to_responses_tools(
    tools: tuple[ToolDefinition, ...],
    *,
    strict: bool = True,
) -> list[dict[str, Any]]:
    """将 ToolDefinition 转换为 Responses API 的扁平工具格式。"""
    result: list[dict[str, Any]] = []
    for tool in tools:
        if tool.builtin is not None:
            result.append(dict(tool.builtin))
            continue

        resolved = tool.parameters or {
            "type": "object",
            "properties": {
                "input": {"type": "string", "description": tool.description}
            },
        }
        if strict:
            resolved = make_schema_strict(resolved)
            result.append(
                {
                    "type": "function",
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": resolved,
                    "strict": True,
                }
            )
        else:
            result.append(
                {
                    "type": "function",
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": resolved,
                }
            )
    return result


def to_responses_input(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """将内部会话消息转换为 Responses API 的 input 项数组。"""
    items: list[dict[str, Any]] = []

    for msg in messages:
        role = str(msg.get("role", "user"))
        content = msg.get("content")

        if role == "tool":
            call_id = str(msg.get("tool_call_id") or "")
            output_str = str(content) if content is not None else ""
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": output_str,
                }
            )
            continue

        if role == "assistant":
            # 处理 tool calls
            tool_calls = msg.get("tool_calls")
            if isinstance(tool_calls, list):
                for call in tool_calls:
                    if not isinstance(call, dict):
                        continue
                    fn = call.get("function", {})
                    args = fn.get("arguments", "{}")
                    items.append(
                        {
                            "type": "function_call",
                            "call_id": str(call.get("id") or ""),
                            "name": str(fn.get("name") or ""),
                            "arguments": (
                                args if isinstance(args, str) else json.dumps(args)
                            ),
                        }
                    )

            if content:
                items.append(
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": str(content)}],
                    }
                )
            continue

        # user 角色
        if isinstance(content, list):
            items.append(
                {
                    "type": "message",
                    "role": "user",
                    "content": content,
                }
            )
        else:
            text_val = str(content) if content is not None else ""
            items.append(
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": text_val}],
                }
            )

    return items


def to_responses_text_config(
    response_format: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """将 response_format 配置转换为 Responses API 的 text.format 格式。"""
    if not response_format:
        return None
    if response_format.get("type") == "json_schema":
        schema_info = response_format.get("json_schema")
        if isinstance(schema_info, dict):
            fmt: dict[str, Any] = {"type": "json_schema"}
            fmt.update(schema_info)
            return {"format": fmt}
    return {"format": response_format}


class OpenAIResponsesProvider:
    """OpenAI Responses API provider。"""

    def __init__(
        self,
        config: ProviderConfig,
        *,
        client: Any | None = None,
        runtime: ProviderRuntime | None = None,
    ) -> None:
        self.config = config
        self.transport = "openai_responses"
        self._usage = UsageAccumulator(config.model)
        self.runtime = runtime or ProviderRuntime()
        self._client = client
        self._current_options: StreamOptions | None = None
        self._metrics: dict[str, object] = {
            "transport": self.transport,
            "sent_messages": 0,
            "cached_tokens": 0,
            "cache_hit_rate": 0.0,
            "prompt_cache_hit_tokens": 0,
            "prompt_cache_miss_tokens": 0,
            "reasoning_tokens": 0,
        }
        self._last_response_id: str | None = None

    @property
    def model(self) -> str:
        return self.config.model

    @property
    def base_url(self) -> str:
        return self.config.base_url

    @property
    def thinking(self) -> bool:
        return self.config.thinking

    @property
    def reasoning_effort(self) -> str | None:
        return self.config.reasoning_effort

    @property
    def context_window(self) -> int | None:
        return self.config.context_window

    @property
    def metrics(self) -> dict[str, object]:
        return self._metrics

    @property
    def usage_totals(self) -> UsageTotals:
        return self._usage.totals

    @property
    def cache_hit_rate(self) -> float | None:
        return self._usage.cache_hit_rate

    def _get_client_and_headers(self) -> tuple[Any, dict[str, str]]:
        """构建或复用 OpenAI SDK 客户端。"""
        if self._client is not None:
            return self._client, {}

        from openai import OpenAI as _OpenAIClient

        api_key = self.config.api_key
        base_url = self.config.base_url or OPENAI_RESPONSES_DEFAULT_BASE_URL
        headers: dict[str, str] = {}

        account_id = self.config.extra.get("account_id")
        if account_id:
            headers["chatgpt-account-id"] = str(account_id)
        if self.config.extra.get("originator"):
            headers["originator"] = str(self.config.extra["originator"])
        else:
            headers["originator"] = "xcode"

        if not api_key:
            import os

            api_key = os.environ.get("OPENAI_API_KEY", "")

        client = _OpenAIClient(
            api_key=api_key or "sk-dummy-no-key",
            base_url=base_url,
            default_headers=headers if headers else None,
        )
        return client, headers

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolDefinition],
        options: StreamOptions | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ProviderEvent]:
        self._current_options = options
        for event in self._stream_sync(messages, tuple(tools)):
            yield event

    def _stream_sync(
        self,
        messages: list[dict[str, Any]],
        tools: tuple[ToolDefinition, ...],
    ) -> Iterator[ProviderEvent]:
        client, extra_headers = self._get_client_and_headers()
        instructions, input_messages = extract_responses_instructions(messages)
        responses_input = to_responses_input(input_messages)
        responses_tools = to_responses_tools(tools)

        params: dict[str, Any] = {
            "model": self.config.model,
            "input": responses_input,
            "stream": True,
            "stream_options": {"include_usage": True},
        }

        if instructions:
            params["instructions"] = instructions

        if responses_tools:
            params["tools"] = responses_tools

        if self.config.response_format:
            text_conf = to_responses_text_config(self.config.response_format)
            if text_conf:
                params["text"] = text_conf

        if self.config.reasoning_effort:
            params["reasoning"] = {"effort": self.config.reasoning_effort}

        # 应用上下文参数
        opts = self._current_options
        if opts:
            if opts.temperature is not None:
                params["temperature"] = opts.temperature
            if opts.max_tokens is not None:
                params["max_output_tokens"] = opts.max_tokens
            if opts.top_p is not None:
                params["top_p"] = opts.top_p
            if opts.tool_choice is not None:
                params["tool_choice"] = opts.tool_choice

        request_headers = dict(extra_headers)
        if opts and opts.headers:
            request_headers.update(opts.headers)
        if request_headers:
            params["extra_headers"] = request_headers

        self._metrics["sent_messages"] = len(input_messages)

        yield from self._decode_responses_stream(client, params)

    def _decode_responses_stream(
        self,
        client: Any,
        params: dict[str, Any],
    ) -> Iterator[ProviderEvent]:
        """调用 client.responses.create 并流式解码事件。"""
        response_stream = client.responses.create(**params)

        function_calls: dict[str, dict[str, str]] = defaultdict(
            lambda: {"id": "", "name": "", "arguments": ""}
        )
        item_to_call_id: dict[str, str] = {}
        ordered_call_ids: list[str] = []
        accumulated_text = ""
        last_response_id: str | None = None

        for event in response_stream:
            event_type = getattr(event, "type", "")

            if event_type == "response.output_text.delta":
                delta_text = getattr(event, "delta", "")
                if delta_text:
                    accumulated_text += str(delta_text)
                    yield TextDelta(chunk=str(delta_text))

            elif event_type in (
                "response.reasoning_text.delta",
                "response.reasoning_summary_text.delta",
                "response.reasoning.delta",
            ):
                delta_text = getattr(event, "delta", "")
                if delta_text:
                    yield ReasoningDelta(chunk=str(delta_text))

            elif event_type == "response.output_item.added":
                item = getattr(event, "item", None)
                if item and getattr(item, "type", "") == "function_call":
                    raw_item_id = str(getattr(item, "id", "") or "")
                    call_id = str(getattr(item, "call_id", "") or raw_item_id)
                    if raw_item_id:
                        item_to_call_id[raw_item_id] = call_id
                    name = str(getattr(item, "name", "") or "")
                    if call_id and call_id not in ordered_call_ids:
                        ordered_call_ids.append(call_id)
                    function_calls[call_id]["id"] = call_id
                    function_calls[call_id]["name"] = name

            elif event_type == "response.function_call_arguments.delta":
                raw_id = str(
                    getattr(event, "call_id", "") or getattr(event, "item_id", "")
                )
                call_id = item_to_call_id.get(raw_id, raw_id)
                delta_args = getattr(event, "delta", "")
                if call_id and delta_args:
                    if call_id not in ordered_call_ids:
                        ordered_call_ids.append(call_id)
                    function_calls[call_id]["arguments"] += str(delta_args)

            elif event_type == "response.function_call_arguments.done":
                raw_id = str(
                    getattr(event, "call_id", "") or getattr(event, "item_id", "")
                )
                call_id = item_to_call_id.get(raw_id, raw_id)
                full_args = getattr(event, "arguments", None)
                if call_id and full_args is not None:
                    function_calls[call_id]["arguments"] = str(full_args)

            elif event_type == "response.completed":
                resp = getattr(event, "response", None)
                if resp:
                    last_response_id = getattr(resp, "id", None)
                    usage = getattr(resp, "usage", None)
                    if usage:
                        prompt_tokens = getattr(usage, "input_tokens", 0) or 0
                        completion_tokens = getattr(usage, "output_tokens", 0) or 0
                        input_details = getattr(usage, "input_tokens_details", None)
                        cached_tokens = (
                            getattr(input_details, "cached_tokens", 0) or 0
                            if input_details
                            else 0
                        )
                        output_details = getattr(usage, "output_tokens_details", None)
                        reasoning_tokens = (
                            getattr(output_details, "reasoning_tokens", 0) or 0
                            if output_details
                            else 0
                        )

                        self._metrics["cached_tokens"] = cached_tokens
                        self._metrics["reasoning_tokens"] = reasoning_tokens

                        cache_usage = CacheUsage(
                            hit_tokens=cached_tokens,
                            miss_tokens=max(0, prompt_tokens - cached_tokens),
                        )
                        self._usage.record(
                            prompt_tokens=prompt_tokens,
                            completion_tokens=completion_tokens,
                            cache_usage=cache_usage,
                        )
                        yield UsageUpdate(
                            input_tokens=prompt_tokens,
                            output_tokens=completion_tokens,
                        )

        if last_response_id:
            self._last_response_id = last_response_id

        # 构建工具调用事件
        ready_calls = [
            ToolCall(
                id=function_calls[cid]["id"],
                name=function_calls[cid]["name"],
                input=parse_tool_arguments(function_calls[cid]["arguments"]),
            )
            for cid in ordered_call_ids
            if cid in function_calls
        ]

        if ready_calls:
            yield ToolCallEvent(calls=ready_calls)
            yield FinalMessage(content=accumulated_text, stop_reason="tool_use")
        else:
            yield FinalMessage(content=accumulated_text, stop_reason="end_turn")
