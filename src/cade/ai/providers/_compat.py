"""OpenAI Chat Completions 兼容 provider 基类。

所有使用 OpenAI Chat Completions API 的 provider（OpenAI、DeepSeek、
ChatGLM、MiMo）共享异步传输与资源生命周期；子类定制请求参数。
"""

from __future__ import annotations

import copy
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any

from cade.ai.cache import extract_cache_usage
from cade.ai.events import ProviderEvent
from cade.ai.types import (
    ProviderConfig,
    StreamOptions,
    ThinkingBudgets,
    ToolDefinition,
)
from cade.ai.usage import UsageAccumulator, UsageTotals

from ._codec import normalize_cross_provider_messages, to_chat_messages, to_chat_tools
from ._stream import chat_stream_to_events


def _lookup_thinking_budget(budgets: ThinkingBudgets, level_name: str) -> int | None:
    if level_name == "minimal":
        return budgets.minimal
    if level_name == "low":
        return budgets.low
    if level_name == "medium":
        return budgets.medium
    if level_name == "high":
        return budgets.high
    if level_name == "xhigh":
        return budgets.xhigh
    return None


class OpenAICompatProvider:
    """OpenAI Chat Completions 兼容基类。

    子类必须设置:
    - self.transport（在 super().__init__ 之前或通过 transport 参数）
    - _build_chat_params(messages, tools, options) 构建请求参数

    子类可覆写:
    - _build_thinking_params() 构建 provider 专有 thinking 参数
    - _clean_reasoning_content() 控制 reasoning_content 保留策略
    - _record_usage() 记录 provider 专有指标
    """

    def __init__(
        self,
        config: ProviderConfig,
        *,
        transport: str = "openai_chat",
    ) -> None:
        self.config = config
        self._usage = UsageAccumulator(config.model)
        self.transport = transport
        self._metrics: dict[str, object] = self._init_metrics()

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
        """配置覆盖的上下文窗口；None 表示使用模型注册表默认值。"""
        return self.config.context_window

    @property
    def metrics(self) -> dict[str, object]:
        return self._metrics

    @property
    def usage_totals(self) -> UsageTotals:
        """本 provider 实例的累计用量与成本。"""
        return self._usage.totals

    @property
    def cache_hit_rate(self) -> float | None:
        """最近一次请求的缓存命中率；尚无 usage 记录时为 None。"""
        return self._usage.cache_hit_rate

    def _init_metrics(self) -> dict[str, object]:
        return {
            "transport": self.transport,
            "sent_messages": 0,
            "cached_tokens": 0,
            "cache_hit_rate": 0.0,
            "prompt_cache_hit_tokens": 0,
            "prompt_cache_miss_tokens": 0,
            "reasoning_tokens": 0,
        }

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolDefinition],
        options: StreamOptions | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[ProviderEvent, None]:
        from openai import APIError, AsyncOpenAI

        messages = self._normalize_messages(messages)
        self._warn_builtin_tools(tuple(tools))
        clean = self._clean_reasoning_content(messages)
        api_messages = to_chat_messages(clean)
        params = self._build_chat_params(api_messages, tuple(tools), options)
        if options:
            if options.api_key:
                params["api_key"] = options.api_key
            headers: dict[str, str] = {}
            if options.session_id:
                headers["x-session-id"] = options.session_id
            if options.headers:
                headers.update(options.headers)
            if headers:
                params["extra_headers"] = headers
        try:
            async with AsyncOpenAI(
                api_key=self.config.api_key,
                base_url=self.config.base_url,
            ) as client:
                stream = await client.chat.completions.create(**params)
                async with stream:
                    async for event in chat_stream_to_events(
                        self._intercept_usage(stream, len(api_messages))
                    ):
                        yield event
        except APIError as exc:
            raise RuntimeError(f"Provider request failed: {exc}") from exc

    def _warn_builtin_tools(self, tools: tuple[ToolDefinition, ...]) -> None:
        """子类可覆写以对不支持的 builtin 工具发出警告。"""

    def _build_chat_params(
        self,
        api_messages: list[dict[str, Any]],
        tools: tuple[ToolDefinition, ...],
        options: StreamOptions | None,
    ) -> dict[str, Any]:
        """构建 Chat Completions 请求参数。子类可覆写添加专有字段。"""
        params: dict[str, Any] = {
            "model": self.config.model,
            "messages": api_messages,
            "tools": to_chat_tools(tools),
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        self._build_thinking_params(params, options=options)
        self._apply_stream_options(params, options)
        return params

    def _apply_stream_options(
        self, params: dict[str, Any], opts: StreamOptions | None
    ) -> None:
        """把通用采样选项下发到 OpenAI-compatible 请求。"""
        if opts is None:
            return
        if opts.temperature is not None:
            params["temperature"] = opts.temperature
        if opts.max_tokens is not None:
            params["max_tokens"] = opts.max_tokens
        if opts.top_p is not None:
            params["top_p"] = opts.top_p
        if opts.tool_choice is not None:
            params["tool_choice"] = opts.tool_choice
        if opts.response_extra_params:
            params.update(opts.response_extra_params)

    # ── metrics 拦截 ──

    async def _intercept_usage(
        self,
        chunks: AsyncIterator[Any],
        message_count: int,
    ) -> AsyncIterator[Any]:
        """拦截流式响应，在遇到 usage 时记录指标。"""
        async for chunk in chunks:
            usage = getattr(chunk, "usage", None)
            if usage:
                self._record_usage(chunk, message_count)
            yield chunk

    def _record_usage(self, response: Any, sent_messages: int) -> None:
        """记录 usage 指标。子类可覆写以添加 provider 专有字段。"""
        self._metrics["sent_messages"] = sent_messages
        usage = getattr(response, "usage", None)
        if usage:
            cache_usage = extract_cache_usage(response)
            self._metrics["cached_tokens"] = cache_usage.hit_tokens
            self._metrics["cache_hit_rate"] = cache_usage.hit_rate
            self._metrics["prompt_cache_hit_tokens"] = cache_usage.hit_tokens
            self._metrics["prompt_cache_miss_tokens"] = cache_usage.miss_tokens

            self._usage.record(
                prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
                cache_usage=cache_usage,
            )

            completion_details = getattr(usage, "completion_tokens_details", None)
            reasoning = (
                getattr(completion_details, "reasoning_tokens", 0)
                if completion_details
                else 0
            )
            self._metrics["reasoning_tokens"] = reasoning or 0

    # ── thinking 参数构建 ──

    def _build_thinking_extra_body(
        self, thinking_override: bool | None = None
    ) -> dict[str, Any]:
        """构建 provider-agnostic thinking extra_body。"""
        effective = (
            self.config.thinking if thinking_override is None else thinking_override
        )
        if effective:
            return {"thinking": {"type": "enabled"}}
        return {"thinking": {"type": "disabled"}}

    def _build_thinking_params(
        self,
        params: dict[str, Any],
        thinking_override: bool | None = None,
        *,
        options: StreamOptions | None = None,
    ) -> None:
        """将 thinking 配置写入 params（extra_body 和 reasoning_effort）。"""
        effective = (
            self.config.thinking if thinking_override is None else thinking_override
        )
        extra = self._build_thinking_extra_body(thinking_override)

        opts = options
        if opts and opts.thinking_budgets and effective:
            level_name = opts.thinking_level
            if level_name and level_name != "off":
                budget = _lookup_thinking_budget(opts.thinking_budgets, level_name)
                if budget and budget > 0:
                    extra.setdefault("thinking", {})["budget_tokens"] = budget

        if extra:
            existing = params.get("extra_body", {})
            existing.update(extra)
            params["extra_body"] = existing
        if effective and self.config.reasoning_effort:
            params["reasoning_effort"] = self.config.reasoning_effort

    # ── 消息归一化 ──

    def _normalize_messages(
        self,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """跨 provider 消息归一化。子类可重写以添加额外转换。"""
        return normalize_cross_provider_messages(messages, self.transport)

    def _clean_reasoning_content(
        self, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """从消息历史中清理 reasoning_content。

        子类可重写以实现不同清理策略。
        默认行为：当 thinking 关闭时删除所有 reasoning_content。
        """
        if not messages or self.config.thinking:
            return messages
        cleaned = copy.deepcopy(messages)
        for msg in cleaned:
            msg.pop("reasoning_content", None)
        return cleaned
