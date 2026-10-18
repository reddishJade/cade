"""OpenAI Responses API provider。

支持 OpenAI /v1/responses 与 ChatGPT 后端 API，
支持 Stateful 对话 (previous_response_id)、扁平 Tool 定义及结构化输出。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import platform
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator, Iterator, Mapping
from inspect import isawaitable
from typing import Any, cast

from cade.ai.cache import CacheUsage
from cade.ai.events import (
    FinalMessage,
    ProviderEvent,
    ProviderJsonValue,
    ProviderReplayPayload,
    ReasoningDelta,
    TextDelta,
    ToolCall,
    ToolCallEvent,
    UsageUpdate,
)
from cade.ai.types import (
    ProviderConfig,
    StreamOptions,
    ToolDefinition,
)
from cade.ai.usage import UsageAccumulator, UsageTotals

from ._codec import make_schema_strict
from ._runtime import ProviderRuntime
from ._stream import parse_tool_arguments

_LOGGER = logging.getLogger(__name__)

OPENAI_RESPONSES_DEFAULT_BASE_URL = "https://api.openai.com/v1"
CHATGPT_BACKEND_BASE_URL = "https://chatgpt.com/backend-api"
_CODEX_PROTOCOL_VERSION = "0.163.0"
_CODEX_RESPONSES_LITE_MODELS = frozenset(
    {
        "gpt-6-astra",
        "gpt-6-sol",
        "gpt-6.1-sol",
        "gpt-6-luna",
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
    }
)
_PROMPT_CACHE_KEY_MAX_LENGTH = 64


def _next_response_event(iterator: Iterator[Any]) -> tuple[bool, Any | None]:
    """在线程中读取同步事件流，避免阻塞 agent 的异步事件循环。"""
    try:
        return True, next(iterator)
    except StopIteration:
        return False, None


async def _iterate_response_events(response_stream: Any) -> AsyncIterator[Any]:
    """统一迭代 SDK 的异步流与兼容客户端提供的同步流。"""
    if hasattr(response_stream, "__aiter__"):
        async for event in response_stream:
            yield event
        return

    iterator = iter(response_stream)
    while True:
        has_event, event = await asyncio.to_thread(_next_response_event, iterator)
        if not has_event:
            return
        yield event


class ProviderRequestError(RuntimeError):
    """将 SDK 请求异常转换为 agent 可识别且不会泄漏响应正文的错误。"""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _codex_sdk_base_url(base_url: str) -> str:
    """把用户配置的 ChatGPT 后端地址转换为 SDK 所需的 Codex 根地址。"""
    normalized = base_url.rstrip("/")
    if normalized.endswith("/codex/responses"):
        return normalized.removesuffix("/responses")
    if normalized.endswith("/codex"):
        return normalized
    return f"{normalized}/codex"


def _codex_user_agent() -> str:
    """生成稳定且可辨识的 ChatGPT Codex 客户端标识。"""
    system = platform.system() or "unknown"
    release = platform.release() or "unknown"
    machine = platform.machine() or "unknown"
    return f"cade-agent ({system} {release}; {machine})"


def _codex_cache_session_id(session_id: str) -> str:
    if len(session_id) <= _PROMPT_CACHE_KEY_MAX_LENGTH:
        return session_id
    return hashlib.sha256(session_id.encode()).hexdigest()


def _format_openai_error(exc: BaseException) -> ProviderRequestError:
    """压缩 OpenAI SDK 异常，避免把 Cloudflare HTML 整页输出到终端。"""
    status_code = getattr(exc, "status_code", None)
    if not isinstance(status_code, int):
        status_code = None

    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", {})
    mitigated = str(headers.get("cf-mitigated", "")).lower() == "challenge"
    raw_message = str(exc)
    body = getattr(exc, "body", None)
    detail = _safe_openai_error_detail(body)
    looks_like_challenge = mitigated or any(
        marker in raw_message.lower()
        for marker in ("<html", "challenge-error-text", "_cf_chl_opt")
    )

    if status_code == 403 and looks_like_challenge:
        message = (
            "ChatGPT backend returned a Cloudflare challenge (HTTP 403). "
            "The request was stopped cleanly; retry after changing the proxy route "
            "or sign in again."
        )
    elif status_code == 401:
        message = (
            "ChatGPT authentication expired or was rejected (HTTP 401); sign in again."
        )
    elif status_code is not None:
        message = f"OpenAI request failed with HTTP {status_code}."
        if detail:
            message = f"{message} {detail}"
    else:
        message = f"OpenAI request failed: {type(exc).__name__}."

    request_id = getattr(exc, "request_id", None)
    if request_id:
        message = f"{message} Request ID: {request_id}."
    return ProviderRequestError(message, status_code=status_code)


def _safe_openai_error_detail(body: object) -> str | None:
    """只从结构化错误体提取短消息，拒绝回显 HTML 或任意长正文。"""
    if not isinstance(body, dict):
        return None
    error = body.get("error", body)
    if not isinstance(error, dict):
        return None
    code = error.get("code")
    context_code = None
    if isinstance(code, str) and code in {
        "context_length_exceeded",
        "context_window_exceeded",
    }:
        context_code = code
    detail = error.get("message")
    if not isinstance(detail, str):
        return context_code
    normalized = " ".join(detail.split())
    if not normalized or "<html" in normalized.lower():
        return context_code
    return f"{context_code}: {normalized[:450]}" if context_code else normalized[:500]


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
                    "strict": False,
                }
            )
    return result


def to_responses_input(
    messages: list[dict[str, Any]],
    *,
    transport: str | None = None,
    model: str | None = None,
) -> list[dict[str, Any]]:
    """将内部会话消息转换为 Responses API 的 input 项数组。"""
    items: list[dict[str, Any]] = []

    for msg in messages:
        role = str(msg.get("role", "user"))
        content = msg.get("content")

        if role == "tool":
            call_id = str(msg.get("tool_call_id") or "")
            output = (
                _responses_content(content)
                if isinstance(content, list)
                else (str(content) if content is not None else "")
            )
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": output,
                }
            )
            continue

        if role == "assistant":
            replay_payload = msg.get("_cade_replay_payload")
            replay_items = _compatible_replay_items(
                replay_payload,
                transport=transport,
                model=model,
                visible_tool_calls=msg.get("tool_calls"),
            )
            if replay_items is not None:
                items.extend(replay_items)
                continue

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
                    "content": _responses_content(content),
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


def _responses_content(content: list) -> list[dict[str, Any]]:
    """把规范格式的文本和图片块转成 Responses 内容项。"""
    parts: list[dict[str, Any]] = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append({"type": "input_text", "text": item.get("text", "")})
        elif isinstance(item, dict) and item.get("type") == "image_url":
            image = item.get("image_url", {})
            parts.append(
                {
                    "type": "input_image",
                    "image_url": image["url"],
                    "detail": image.get("detail", "auto"),
                }
            )
        else:
            parts.append(item)
    return parts


def _compatible_replay_items(
    value: object,
    *,
    transport: str | None,
    model: str | None,
    visible_tool_calls: object,
) -> list[dict[str, Any]] | None:
    if not isinstance(value, Mapping):
        return None
    if (
        value.get("protocol") != "openai_responses"
        or value.get("transport") != transport
        or value.get("model") != model
    ):
        return None
    raw_items = value.get("output_items")
    if not isinstance(raw_items, (list, tuple)):
        return None
    items: list[dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, Mapping):
            return None
        items.append(dict(item))
    if not items:
        return None

    visible_call_ids: list[str] = []
    if isinstance(visible_tool_calls, list):
        for call in visible_tool_calls:
            if not isinstance(call, Mapping):
                return None
            call_id = call.get("id")
            if not isinstance(call_id, str) or not call_id:
                return None
            visible_call_ids.append(call_id)
    elif visible_tool_calls is not None:
        return None

    replay_call_ids: list[str] = []
    has_reasoning_item = False
    for item in items:
        if item.get("type") == "reasoning":
            has_reasoning_item = True
        elif item.get("type") == "function_call":
            call_id = item.get("call_id")
            if not isinstance(call_id, str) or not call_id:
                return None
            replay_call_ids.append(call_id)
    if replay_call_ids != visible_call_ids:
        return None

    if has_reasoning_item and not any(
        _optional_response_int(value, name) is not None
        for name in ("reasoning_tokens", "output_tokens", "output_token_limit")
    ):
        return None
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


def _response_value(value: object, name: str, default: Any = None) -> Any:
    """同时读取 SDK 模型和兼容 Responses 端点返回的字典事件。"""
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _response_json_value(value: object) -> ProviderJsonValue:
    """按 SDK wire alias 将已完成的输出项转换为 JSON 值。"""
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        value = model_dump(mode="json", by_alias=True, exclude_none=True)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        result: dict[str, ProviderJsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("Responses output item keys must be strings")
            result[key] = _response_json_value(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_response_json_value(item) for item in value]
    raise TypeError("Responses output item contains a non-JSON value")


def _response_replay_item(value: object) -> dict[str, ProviderJsonValue]:
    item = _response_json_value(value)
    if not isinstance(item, dict):
        raise TypeError("Responses output item must be an object")
    return item


def _merge_completed_output_items(
    streamed_items: list[tuple[int | None, dict[str, ProviderJsonValue]]],
    response_items: list[tuple[int, dict[str, ProviderJsonValue]]],
) -> list[tuple[int | None, dict[str, ProviderJsonValue]]] | None:
    indexed: dict[int, dict[str, ProviderJsonValue]] = {}
    unindexed: list[dict[str, ProviderJsonValue]] = []

    for index, item in streamed_items:
        if index is None:
            item_id = _replay_item_id(item)
            if item not in unindexed and not (
                item_id is not None
                and any(_replay_item_id(existing) == item_id for existing in unindexed)
            ):
                unindexed.append(item)
            continue
        existing = indexed.get(index)
        if existing is not None and existing != item:
            return None
        item_id = _replay_item_id(item)
        already_present = (
            any(
                _replay_item_id(existing_item) == item_id
                for existing_item in indexed.values()
            )
            if item_id is not None
            else item in indexed.values()
        )
        if not already_present:
            indexed[index] = item

    for index, item in response_items:
        item_id = _replay_item_id(item)
        matching_unindexed = next(
            (
                unindexed_index
                for unindexed_index, existing_item in enumerate(unindexed)
                if (item_id is not None and _replay_item_id(existing_item) == item_id)
                or (item_id is None and existing_item == item)
            ),
            None,
        )
        if matching_unindexed is not None:
            unindexed[matching_unindexed] = item
            continue
        matching_index = next(
            (
                existing_index
                for existing_index, existing_item in indexed.items()
                if (item_id is not None and _replay_item_id(existing_item) == item_id)
                or (item_id is None and existing_item == item)
            ),
            None,
        )
        if matching_index is not None:
            indexed[matching_index] = item
            continue
        if item in unindexed:
            unindexed.remove(item)
            indexed[index] = item
            continue
        existing = indexed.get(index)
        if existing is not None:
            if existing != item:
                return None
            indexed[index] = item
            continue
        indexed[index] = item

    indexed_ids = {
        item_id
        for item in indexed.values()
        if (item_id := _replay_item_id(item)) is not None
    }
    unindexed = [
        item
        for item in unindexed
        if _replay_item_id(item) not in indexed_ids and item not in indexed.values()
    ]
    return [
        *((index, item) for index, item in sorted(indexed.items())),
        *((None, item) for item in unindexed),
    ]


def _replay_item_id(item: Mapping[str, ProviderJsonValue]) -> str | None:
    item_id = item.get("id")
    return item_id if isinstance(item_id, str) and item_id else None


def _optional_response_int(value: object, name: str) -> int | None:
    result = _response_value(value, name)
    if isinstance(result, int) and not isinstance(result, bool) and result >= 0:
        return result
    return None


def _completed_replay_payload(
    indexed_items: list[tuple[int | None, dict[str, ProviderJsonValue]]],
    transport: str,
    model: str,
    reasoning_tokens: int | None,
    output_tokens: int | None,
    output_token_limit: int | None,
) -> ProviderReplayPayload | None:
    if not indexed_items:
        return None
    indices = [index for index, _ in indexed_items]
    if any(index is not None for index in indices):
        if any(index is None for index in indices):
            return None
        ordered = sorted(indexed_items, key=lambda entry: cast(int, entry[0]))
        if [index for index, _ in ordered] != list(range(len(ordered))):
            return None
    else:
        ordered = indexed_items
    return ProviderReplayPayload(
        protocol="openai_responses",
        transport=transport,
        model=model,
        output_items=tuple(item for _, item in ordered),
        reasoning_tokens=reasoning_tokens,
        output_tokens=output_tokens,
        output_token_limit=output_token_limit,
    )


def _response_int(value: object, name: str) -> int:
    """从 SDK 模型或字典事件中安全读取整数字段。"""
    result = _response_value(value, name, 0)
    if isinstance(result, int) and not isinstance(result, bool):
        return result
    return 0


def _response_tool_arguments(value: object) -> str:
    arguments = _response_value(value, "arguments")
    if isinstance(arguments, str):
        return arguments
    if arguments is None:
        return "{}"
    return json.dumps(arguments)


def _reasoning_summary_text(item: object) -> str:
    """从完整 reasoning item 中提取服务端生成的可见摘要。"""
    summary = _response_value(item, "summary")
    if not isinstance(summary, (list, tuple)):
        return ""
    parts = [str(text) for part in summary if (text := _response_value(part, "text"))]
    return "\n\n".join(parts)


def _reasoning_item_key(value: object, fallback: str = "reasoning") -> str:
    """为同一 reasoning 输出项生成跨事件稳定的键。"""
    output_index = _response_value(value, "output_index")
    if isinstance(output_index, int):
        return f"output:{output_index}"
    item_id = _response_value(value, "item_id") or _response_value(value, "id")
    return f"item:{item_id}" if item_id else fallback


def _unseen_reasoning_suffix(previous: str, complete: str) -> str:
    """用 done 事件补齐未流式送达的摘要，同时避免重复显示。"""
    if not complete or complete == previous:
        return ""
    if not previous:
        return complete
    if complete.startswith(previous):
        return complete[len(previous) :]
    if previous.startswith(complete):
        return ""
    return ""


class OpenAIResponsesProvider:
    """OpenAI Responses API 适配器。"""

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

        from openai import AsyncOpenAI as _OpenAIClient

        api_key = self.config.api_key
        base_url = self.config.base_url or OPENAI_RESPONSES_DEFAULT_BASE_URL
        headers: dict[str, str] = {}

        account_id = self.config.extra.get("account_id")
        if account_id:
            headers["chatgpt-account-id"] = str(account_id)
        if self.config.extra.get("originator"):
            headers["originator"] = str(self.config.extra["originator"])
        else:
            headers["originator"] = "cade"

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
        from openai import APIError

        try:
            async for event in self._stream(messages, tuple(tools)):
                yield event
        except APIError as exc:
            raise _format_openai_error(exc) from exc

    async def _stream(
        self,
        messages: list[dict[str, Any]],
        tools: tuple[ToolDefinition, ...],
    ) -> AsyncIterator[ProviderEvent]:
        owns_client = self._client is None
        client, extra_headers = self._get_client_and_headers()
        try:
            instructions, input_messages = extract_responses_instructions(messages)
            responses_input = to_responses_input(
                input_messages,
                transport=self.transport,
                model=self.config.model,
            )
            responses_tools = to_responses_tools(tools, strict=self._strict_tools())

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

            # 应用上下文参数
            opts = self._current_options
            reasoning: dict[str, str] = {}
            if self.config.reasoning_effort:
                reasoning["effort"] = self.config.reasoning_effort
            if self.config.thinking:
                reasoning["summary"] = (
                    opts.reasoning_summary
                    if opts and opts.reasoning_summary is not None
                    else "auto"
                )
            if reasoning:
                params["reasoning"] = reasoning

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

            self._finalize_request_params(params)

            self._metrics["sent_messages"] = len(input_messages)

            async for event in self._decode_responses_stream(client, params):
                yield event
        finally:
            if owns_client:
                await client.close()

    def _finalize_request_params(self, params: dict[str, Any]) -> None:
        """允许专用 transport 在发送前补充 Responses 请求参数。"""

    def _strict_tools(self) -> bool:
        """普通 OpenAI Responses 默认使用 strict function schema。"""
        return True

    async def _decode_responses_stream(
        self,
        client: Any,
        params: dict[str, Any],
    ) -> AsyncIterator[ProviderEvent]:
        """调用 client.responses.create 并流式解码事件。"""
        response_stream = client.responses.create(**params)
        if isawaitable(response_stream):
            response_stream = await response_stream

        function_calls: dict[str, dict[str, str]] = defaultdict(
            lambda: {"id": "", "name": "", "arguments": ""}
        )
        item_to_call_id: dict[str, str] = {}
        ordered_call_ids: list[str] = []
        accumulated_text = ""
        accumulated_reasoning: dict[str, str] = defaultdict(str)
        completed_output_items: list[
            tuple[int | None, dict[str, ProviderJsonValue]]
        ] = []
        completed_call_ids: set[str] = set()
        replay_items_valid = True
        response_completed = False
        reasoning_tokens_for_replay: int | None = None
        output_tokens_for_replay: int | None = None
        output_token_limit = _optional_response_int(params, "max_output_tokens")
        last_response_id: str | None = None

        async for event in _iterate_response_events(response_stream):
            event_type = _response_value(event, "type", "")

            if event_type == "response.output_text.delta":
                delta_text = _response_value(event, "delta", "")
                if delta_text:
                    accumulated_text += str(delta_text)
                    yield TextDelta(chunk=str(delta_text))

            elif event_type in (
                "response.reasoning_text.delta",
                "response.reasoning_summary_text.delta",
                "response.reasoning.delta",
            ):
                delta_text = _response_value(event, "delta", "")
                if delta_text:
                    key = _reasoning_item_key(event)
                    accumulated_reasoning[key] += str(delta_text)
                    yield ReasoningDelta(chunk=str(delta_text))

            elif event_type in (
                "response.reasoning_text.done",
                "response.reasoning_summary_text.done",
            ):
                complete_text = str(_response_value(event, "text", "") or "")
                key = _reasoning_item_key(event)
                suffix = _unseen_reasoning_suffix(
                    accumulated_reasoning[key], complete_text
                )
                if suffix:
                    accumulated_reasoning[key] += suffix
                    yield ReasoningDelta(chunk=suffix)

            elif event_type in (
                "response.output_item.added",
                "response.output_item.done",
            ):
                item = _response_value(event, "item")
                if event_type == "response.output_item.done":
                    replay_item = _response_replay_item(item)
                    completed_output_items.append(
                        (
                            _optional_response_int(event, "output_index"),
                            replay_item,
                        )
                    )
                    item_id = str(_response_value(item, "id", "") or "")
                    if replay_item.get("type") == "function_call":
                        call_id = str(replay_item.get("call_id") or item_id)
                        if call_id:
                            completed_call_ids.add(call_id)
                item_type = _response_value(item, "type", "") if item else ""
                if item_type == "reasoning":
                    key = _reasoning_item_key(event, _reasoning_item_key(item))
                    complete_text = _reasoning_summary_text(item)
                    suffix = _unseen_reasoning_suffix(
                        accumulated_reasoning[key], complete_text
                    )
                    if suffix:
                        accumulated_reasoning[key] += suffix
                        yield ReasoningDelta(chunk=suffix)
                elif item_type == "function_call":
                    raw_item_id = str(_response_value(item, "id", "") or "")
                    call_id = str(_response_value(item, "call_id", "") or raw_item_id)
                    if raw_item_id:
                        item_to_call_id[raw_item_id] = call_id
                    name = str(_response_value(item, "name", "") or "")
                    if call_id and call_id not in ordered_call_ids:
                        ordered_call_ids.append(call_id)
                    function_calls[call_id]["id"] = call_id
                    function_calls[call_id]["name"] = name
                    function_calls[call_id]["arguments"] = _response_tool_arguments(
                        item
                    )

            elif event_type == "response.function_call_arguments.delta":
                raw_id = str(
                    _response_value(event, "call_id", "")
                    or _response_value(event, "item_id", "")
                )
                call_id = item_to_call_id.get(raw_id, raw_id)
                delta_args = _response_value(event, "delta", "")
                if call_id and delta_args:
                    if call_id not in ordered_call_ids:
                        ordered_call_ids.append(call_id)
                    function_calls[call_id]["arguments"] += str(delta_args)

            elif event_type == "response.function_call_arguments.done":
                raw_id = str(
                    _response_value(event, "call_id", "")
                    or _response_value(event, "item_id", "")
                )
                call_id = item_to_call_id.get(raw_id, raw_id)
                full_args = _response_value(event, "arguments")
                if call_id and full_args is not None:
                    function_calls[call_id]["arguments"] = str(full_args)

            elif event_type in (
                "response.completed",
                "response.done",
                "response.incomplete",
            ):
                resp = _response_value(event, "response")
                if resp:
                    response_status = _response_value(resp, "status")
                    if event_type == "response.completed":
                        response_completed = True
                    elif event_type == "response.done":
                        response_completed = response_status in (None, "completed")
                    elif event_type == "response.incomplete":
                        response_completed = False
                    last_response_id = _response_value(resp, "id")
                    response_output = _response_value(resp, "output", ())
                    if isinstance(response_output, (list, tuple)):
                        response_items: list[
                            tuple[int, dict[str, ProviderJsonValue]]
                        ] = []
                        for output_index, item in enumerate(response_output):
                            item_status = _response_value(item, "status")
                            if item_status not in (None, "completed"):
                                continue
                            item_type = _response_value(item, "type", "")
                            if response_completed:
                                replay_item = _response_replay_item(item)
                                response_items.append((output_index, replay_item))
                                item_id = str(replay_item.get("id") or "")
                                if replay_item.get("type") == "function_call":
                                    call_id = str(replay_item.get("call_id") or item_id)
                                    if call_id:
                                        completed_call_ids.add(call_id)
                                        if call_id not in ordered_call_ids:
                                            ordered_call_ids.append(call_id)
                                        function_calls[call_id]["id"] = call_id
                                        function_calls[call_id]["name"] = str(
                                            replay_item.get("name") or ""
                                        )
                                        function_calls[call_id]["arguments"] = (
                                            _response_tool_arguments(replay_item)
                                        )
                            if item_type == "reasoning":
                                key = f"output:{output_index}"
                                complete_text = _reasoning_summary_text(item)
                                suffix = _unseen_reasoning_suffix(
                                    accumulated_reasoning[key], complete_text
                                )
                                if suffix:
                                    accumulated_reasoning[key] += suffix
                                    yield ReasoningDelta(chunk=suffix)
                        if response_completed:
                            merged_items = _merge_completed_output_items(
                                completed_output_items,
                                response_items,
                            )
                            if merged_items is None:
                                replay_items_valid = False
                            else:
                                completed_output_items = merged_items
                    usage = _response_value(resp, "usage")
                    if usage:
                        prompt_tokens = _response_int(usage, "input_tokens")
                        completion_tokens = _response_int(usage, "output_tokens")
                        input_details = _response_value(usage, "input_tokens_details")
                        cached_tokens = _response_int(input_details, "cached_tokens")
                        output_details = _response_value(usage, "output_tokens_details")
                        reasoning_tokens = _response_int(
                            output_details, "reasoning_tokens"
                        )
                        reasoning_tokens_for_replay = _optional_response_int(
                            output_details, "reasoning_tokens"
                        )
                        output_tokens_for_replay = _optional_response_int(
                            usage, "output_tokens"
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
            if cid in function_calls and cid in completed_call_ids
        ]

        if ready_calls and response_completed:
            yield ToolCallEvent(calls=ready_calls)
            yield FinalMessage(
                content=accumulated_text,
                stop_reason="tool_use",
                replay_payload=_completed_replay_payload(
                    completed_output_items,
                    self.transport,
                    self.config.model,
                    reasoning_tokens_for_replay,
                    output_tokens_for_replay,
                    output_token_limit,
                )
                if replay_items_valid
                else None,
            )
        else:
            yield FinalMessage(
                content=accumulated_text,
                stop_reason="end_turn" if response_completed else "max_tokens",
                replay_payload=(
                    _completed_replay_payload(
                        completed_output_items,
                        self.transport,
                        self.config.model,
                        reasoning_tokens_for_replay,
                        output_tokens_for_replay,
                        output_token_limit,
                    )
                    if response_completed and replay_items_valid
                    else None
                ),
            )


class OpenAICodexResponsesProvider(OpenAIResponsesProvider):
    """使用 ChatGPT 登录态调用 Codex Responses 后端。"""

    supports_output_token_limit = False

    def __init__(
        self,
        config: ProviderConfig,
        *,
        client: Any | None = None,
        runtime: ProviderRuntime | None = None,
    ) -> None:
        super().__init__(config, client=client, runtime=runtime)
        self.transport = "openai_codex"
        self._metrics["transport"] = self.transport
        self._default_session_id = str(uuid.uuid4())

    def _get_client_and_headers(self) -> tuple[Any, dict[str, str]]:
        """获取 ChatGPT Codex 客户端和请求头。"""
        if self._client is not None:
            return self._client, self._codex_headers()

        import os

        from openai import AsyncOpenAI as _OpenAIClient

        api_key = self.config.api_key or os.environ.get("OPENAI_API_KEY", "")
        configured_url = self.config.base_url or CHATGPT_BACKEND_BASE_URL
        headers = self._codex_headers()
        client = _OpenAIClient(
            api_key=api_key or "sk-dummy-no-key",
            base_url=_codex_sdk_base_url(configured_url),
            default_headers=headers,
            max_retries=0,
        )
        return client, headers

    def _codex_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
            "OpenAI-Beta": "responses=experimental",
            "version": _CODEX_PROTOCOL_VERSION,
            "User-Agent": _codex_user_agent(),
            "originator": str(self.config.extra.get("originator") or "cade"),
        }
        if self.config.model in _CODEX_RESPONSES_LITE_MODELS:
            headers["x-openai-internal-codex-responses-lite"] = "true"
        account_id = self.config.extra.get("account_id")
        if account_id:
            headers["chatgpt-account-id"] = str(account_id)
        return headers

    def _finalize_request_params(self, params: dict[str, Any]) -> None:
        params.pop("stream_options", None)
        params["store"] = False
        params["include"] = ["reasoning.encrypted_content"]
        uses_responses_lite = self.config.model in _CODEX_RESPONSES_LITE_MODELS
        params["parallel_tool_calls"] = not uses_responses_lite
        params.setdefault("tool_choice", "auto")

        options = self._current_options
        requested_session_id = (
            options.session_id
            if options and options.session_id
            else self._default_session_id
        )
        session_id = _codex_cache_session_id(requested_session_id)
        request_headers = params.setdefault("extra_headers", {})
        for header in ("session-id", "thread-id"):
            for existing in tuple(request_headers):
                if existing.lower() == header:
                    del request_headers[existing]
            request_headers[header] = session_id
        params["prompt_cache_key"] = session_id

        if uses_responses_lite:
            instructions = params.pop("instructions", None) or ""
            tools = params.pop("tools", [])
            params["input"] = [
                {"type": "additional_tools", "role": "developer", "tools": tools},
                {
                    "type": "message",
                    "role": "developer",
                    "content": [{"type": "input_text", "text": instructions}],
                },
                *params.get("input", []),
            ]
            reasoning = params.setdefault("reasoning", {})
            reasoning.setdefault("effort", self.config.reasoning_effort or "none")
            reasoning.setdefault("summary", "auto")
            reasoning["context"] = "all_turns"
        elif self.config.thinking:
            reasoning = params.setdefault("reasoning", {})
            reasoning.setdefault("summary", "auto")

    def _strict_tools(self) -> bool:
        """Codex 显式关闭 strict，保留工具的可选参数与联合 schema。"""
        return False
