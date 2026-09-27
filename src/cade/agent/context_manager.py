"""会话级上下文管理器。

把历史、world state、预算、压缩生命周期和 provider 统计放在同一个可追踪对象中。
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from ._context_window import estimate_message_tokens, estimate_wire_message_tokens
from ._tool_pairing import repair_tool_pairing
from .context import ContextState
from .context_policy import ContextSnapshot
from .messages import AgentMessage

if TYPE_CHECKING:
    from cade.ai.providers.base import StreamProvider
    from cade.ai.types import StreamOptions, ToolDefinition

    from .request import RequestAssembly


@dataclass
class ContextTokenUsage:
    """当前 context window 的估算和 provider 实测 token。"""

    estimated_prompt_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    context_budget: int = 0
    budget_remaining: int = 0
    last_prompt_tokens: int | None = None


@dataclass
class ContextWindowState:
    """context window 生命周期状态。"""

    context_window_id: int = 0
    reset_count: int = 0
    last_reason: str | None = None
    last_messages_before: int = 0
    last_messages_after: int = 0


@dataclass
class PromptCacheMetadata:
    """最近一次请求的 prompt/cache fingerprint。"""

    prompt_digest: str = ""
    request_digest: str = ""
    system_prompt_bytes: int = 0
    request_count: int = 0


@dataclass(frozen=True)
class _RequestTokenAnchor:
    """只保存请求指纹；实测输入已包含固定前缀和工具开销。"""

    messages: tuple[str, ...]
    configuration: str
    prompt_tokens: int = 0
    message_tokens: tuple[int, ...] = ()
    source_messages: tuple[str, ...] = ()


@dataclass
class ContextManager:
    """会话上下文的唯一可变状态边界。"""

    history: list[AgentMessage] = field(default_factory=list)
    context_state: ContextState = field(default_factory=ContextState)
    history_version: int = 0
    token_usage: ContextTokenUsage = field(default_factory=ContextTokenUsage)
    context_window: ContextWindowState = field(default_factory=ContextWindowState)
    prompt_cache: PromptCacheMetadata = field(default_factory=PromptCacheMetadata)
    provider_usage: dict[str, int] = field(default_factory=dict)
    context_snapshot: ContextSnapshot | None = None
    _provider_key: tuple[object, ...] | None = field(default=None, repr=False)
    _pending_request: _RequestTokenAnchor | None = field(default=None, repr=False)
    _token_anchor: _RequestTokenAnchor | None = field(default=None, repr=False)

    def bind_provider(self, provider: StreamProvider) -> None:
        """模型、连接或推理配置变化后丢弃旧请求锚点。"""
        key = (
            id(provider),
            getattr(provider, "model", None),
            getattr(provider, "transport", None),
            getattr(provider, "base_url", None),
            getattr(provider, "thinking", None),
            getattr(provider, "reasoning_effort", None),
        )
        if key != self._provider_key:
            self.invalidate_token_anchor()
            self._provider_key = key

    def invalidate_token_anchor(self) -> None:
        """旧窗口或失败请求的统计不能成为新窗口的基线。"""
        self._pending_request = None
        self._token_anchor = None
        self.token_usage.last_prompt_tokens = None

    def estimate_request_tokens(
        self,
        messages: Sequence[Mapping[str, object]],
        tools: Sequence[ToolDefinition],
        options: StreamOptions | None,
        local_tokens: int,
        *,
        prefix_length: int = 0,
        source_messages: tuple[str, ...] = (),
    ) -> tuple[int, bool]:
        """保留实测基线；固定前缀与事实历史未变时允许派生投影增减。"""
        anchor = self._token_anchor
        if anchor is None or anchor.configuration != _request_configuration(
            tools, options
        ):
            return local_tokens, False
        position = 0
        added_tokens = 0
        for message in messages:
            payload = _request_json(message)
            digest = _digest(payload)
            if position < len(anchor.messages) and digest == anchor.messages[position]:
                position += 1
            else:
                # 给新增消息的角色和边界留少量开销；不重复估算已实测内容。
                added_tokens += estimate_wire_message_tokens(message)
        if position != len(anchor.messages):
            digests = tuple(_digest(_request_json(m)) for m in messages)
            if (
                prefix_length <= 0
                or anchor.messages[:prefix_length] != digests[:prefix_length]
                or not anchor.source_messages
                or not _is_subsequence(anchor.source_messages, source_messages)
                or len(anchor.message_tokens) != len(anchor.messages)
            ):
                return local_tokens, False
            # 只估算投影改变的消息，不重新估算已实测的固定成本。
            remaining = Counter(digests)
            delta = 0
            for digest, tokens in zip(
                anchor.messages, anchor.message_tokens, strict=True
            ):
                if remaining[digest] > 0:
                    remaining[digest] -= 1
                else:
                    delta -= tokens
            for message, digest in zip(messages, digests, strict=True):
                if remaining[digest] > 0:
                    delta += estimate_wire_message_tokens(message)
                    remaining[digest] -= 1
            return max(1, anchor.prompt_tokens + delta), True
        return anchor.prompt_tokens + added_tokens, True

    def history_messages(self) -> list[AgentMessage]:
        return list(self.history)

    def replace_history(
        self,
        messages: Sequence[AgentMessage],
        *,
        normalize: bool = True,
    ) -> list[AgentMessage]:
        """替换历史并在进入 session surface 前修复工具配对。"""
        replacement = list(messages)
        if normalize:
            replacement = repair_tool_pairing(replacement)
        self.history[:] = replacement
        self.history_version += 1
        self.token_usage.estimated_prompt_tokens = estimate_message_tokens(self.history)
        return self.history_messages()

    def normalize_messages(
        self,
        messages: Sequence[AgentMessage],
    ) -> list[AgentMessage]:
        """在 provider 请求前返回已修复工具配对的消息投影。"""
        return repair_tool_pairing(list(messages))

    def append(self, messages: Sequence[AgentMessage]) -> None:
        if not messages:
            return
        self.history.extend(messages)
        self.history[:] = repair_tool_pairing(self.history)
        self.history_version += 1
        self.token_usage.estimated_prompt_tokens = estimate_message_tokens(self.history)

    def complete_rollover(
        self,
        messages: Sequence[AgentMessage],
        *,
        reason: str = "token_limit",
        before_messages: int | None = None,
    ) -> list[AgentMessage]:
        """提交新活动窗口并重置窗口级动态状态。"""
        before = len(self.history) if before_messages is None else before_messages
        replacement = self.replace_history(messages)
        self.context_state.reset()
        self.context_snapshot = None
        self.invalidate_token_anchor()
        self.context_window.context_window_id += 1
        self.context_window.reset_count += 1
        self.context_window.last_reason = reason
        self.context_window.last_messages_before = before
        self.context_window.last_messages_after = len(replacement)
        return replacement

    def clear(self) -> None:
        self.history.clear()
        self.context_snapshot = None
        self.context_state.reset()
        self.history_version += 1
        self.token_usage = ContextTokenUsage()
        self.context_window = ContextWindowState(
            context_window_id=self.context_window.context_window_id + 1
        )
        self.provider_usage.clear()
        self.prompt_cache = PromptCacheMetadata()
        self.invalidate_token_anchor()

    def set_last_prompt_tokens(self, value: int | None) -> None:
        self.invalidate_token_anchor()
        self.token_usage.last_prompt_tokens = value

    def record_request(self, assembly: RequestAssembly) -> None:
        """记录组装后的请求预算和 prompt/cache fingerprint。"""
        self.context_snapshot = assembly.context_snapshot
        self.token_usage.estimated_prompt_tokens = assembly.estimated_tokens
        self.token_usage.context_budget = assembly.token_budget
        self.token_usage.budget_remaining = assembly.budget_remaining
        wire_messages = list(assembly.wire_messages)
        self._pending_request = _RequestTokenAnchor(
            messages=tuple(
                _digest(_request_json(message)) for message in wire_messages
            ),
            configuration=_request_configuration(assembly.tools, assembly.options),
            message_tokens=tuple(
                estimate_wire_message_tokens(m) for m in wire_messages
            ),
            source_messages=assembly.source_message_digests,
        )
        system_prompt = "\n\n".join(
            str(message.get("content", ""))
            for message in wire_messages
            if message.get("role") == "system"
        )
        prompt_bytes = system_prompt.encode("utf-8")
        request_payload = {
            "messages": wire_messages,
            "tools": [_json_value(tool) for tool in assembly.tools],
        }
        request_bytes = json.dumps(
            request_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        self.prompt_cache = PromptCacheMetadata(
            prompt_digest=hashlib.blake2b(prompt_bytes, digest_size=32).hexdigest(),
            request_digest=hashlib.blake2b(request_bytes, digest_size=32).hexdigest(),
            system_prompt_bytes=len(prompt_bytes),
            request_count=self.prompt_cache.request_count + 1,
        )

    def record_provider_usage(
        self, usage: Mapping[str, object] | None, *, request_succeeded: bool = True
    ) -> None:
        """累加 provider 返回的输入、输出和缓存统计。"""
        if not usage:
            self._pending_request = None
            return
        numeric: dict[str, int] = {}
        for key, value in usage.items():
            if isinstance(value, int) and not isinstance(value, bool):
                numeric[str(key)] = value
        for key, value in numeric.items():
            self.provider_usage[key] = self.provider_usage.get(key, 0) + value

        prompt_tokens = numeric.get("prompt_tokens")
        completion_tokens = numeric.get("completion_tokens")
        if prompt_tokens is not None:
            self.token_usage.prompt_tokens += prompt_tokens
            if request_succeeded and prompt_tokens > 0:
                self.token_usage.last_prompt_tokens = prompt_tokens
            if (
                request_succeeded
                and prompt_tokens > 0
                and self._pending_request is not None
            ):
                pending = self._pending_request
                self._token_anchor = replace(pending, prompt_tokens=prompt_tokens)
        self._pending_request = None
        if completion_tokens is not None:
            self.token_usage.completion_tokens += completion_tokens
        total_tokens = numeric.get("total_tokens")
        if total_tokens is not None:
            self.token_usage.total_tokens += total_tokens
        else:
            self.token_usage.total_tokens += prompt_tokens or 0
            self.token_usage.total_tokens += completion_tokens or 0


def _request_json(value: object) -> str:
    return json.dumps(
        _json_value(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _digest(payload: str) -> str:
    return hashlib.blake2b(payload.encode("utf-8"), digest_size=32).hexdigest()


def _request_configuration(
    tools: Sequence[ToolDefinition], options: StreamOptions | None
) -> str:
    return _digest(
        _request_json(
            {
                "tools": list(tools),
                "transport": options.transport if options else None,
                "reasoning": options.reasoning if options else None,
                "reasoning_summary": options.reasoning_summary if options else None,
            }
        )
    )


def _json_value(value: object) -> Any:
    if hasattr(value, "__dict__"):
        return {
            str(key): _json_value(item)
            for key, item in vars(value).items()
            if not str(key).startswith("_")
        }
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_value(item) for item in value]
    return value


def history_fingerprints(messages: Sequence[AgentMessage]) -> tuple[str, ...]:
    """事实消息指纹用于区分准入投影与 replay/fork/undo 等历史替换。"""
    return tuple(_digest(_request_json(m.model_dump(mode="json"))) for m in messages)


def _is_subsequence(previous: tuple[str, ...], current: tuple[str, ...]) -> bool:
    position = 0
    for digest in current:
        if position < len(previous) and digest == previous[position]:
            position += 1
    return position == len(previous)
