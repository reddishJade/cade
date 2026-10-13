"""会话级上下文管理器。

持有活动历史与 provider 输入 token 校准锚点；请求预算和审计由请求组装器提供。
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from ._context_window import estimate_wire_message_tokens
from ._tool_pairing import repair_tool_pairing
from .messages import AgentMessage

if TYPE_CHECKING:
    from cade.ai.providers.base import StreamProvider
    from cade.ai.types import StreamOptions, ToolDefinition

    from .request import RequestAssembly


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
    last_prompt_tokens: int | None = None
    last_rotation_reason: str | None = None
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
        self.last_prompt_tokens = None

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

    def complete_rollover(
        self,
        messages: Sequence[AgentMessage],
        *,
        reason: str = "token_limit",
    ) -> list[AgentMessage]:
        """提交新活动窗口并重置窗口级动态状态。"""
        replacement = self.replace_history(messages)
        self.invalidate_token_anchor()
        self.last_rotation_reason = reason
        return replacement

    def clear(self) -> None:
        self.history.clear()
        self.last_rotation_reason = None
        self.invalidate_token_anchor()

    def record_request(self, assembly: RequestAssembly) -> None:
        """保存请求指纹，供成功响应的输入用量建立校准锚点。"""
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

    def record_provider_usage(
        self, usage: Mapping[str, object] | None, *, request_succeeded: bool = True
    ) -> None:
        """用成功响应的实测输入量校准请求；累计用量由 provider 负责。"""
        prompt_tokens = usage.get("prompt_tokens") if usage else None
        if (
            request_succeeded
            and isinstance(prompt_tokens, int)
            and not isinstance(prompt_tokens, bool)
            and prompt_tokens > 0
        ):
            self.last_prompt_tokens = prompt_tokens
            if self._pending_request is not None:
                self._token_anchor = replace(
                    self._pending_request, prompt_tokens=prompt_tokens
                )
        self._pending_request = None


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
