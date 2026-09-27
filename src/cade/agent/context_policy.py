"""请求预算策略：输出预留、安全余量与工作窗口共用一个边界。"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace

from cade.ai.models import get_model_context_window
from cade.ai.providers.base import StreamProvider
from cade.ai.types import StreamOptions

from ._codec import convert_to_llm
from ._context_window import (
    estimate_message_tokens,
    estimate_tokens,
    estimate_wire_message_tokens,
)
from .messages import (
    AgentMessage,
    AssistantMessage,
    SystemMessage,
    ToolResultMessage,
    UserMessage,
)
from .types import TextContent, ToolCallContent


@dataclass(frozen=True)
class ContextSnapshot:
    """请求预算与生命周期占用；分项只作本地估算，不伪装成 provider 实测。"""

    physical_window: int | None
    effective_input_budget: int
    fixed_prefix_tokens: int
    durable_tokens: int
    working_tokens: int
    evidence_tokens: int
    category_total_tokens: int
    total_input_tokens: int
    total_input_source: str
    output_reserve_tokens: int
    operational_headroom_tokens: int
    remaining_input_budget: int
    current_window_id: str | None
    last_rotation_reason: str | None
    rotation_blocked_reason: str | None
    category_source: str = "local"


@dataclass(frozen=True)
class ContextPolicy:
    """固定前缀、持久状态和工作消息共享输入预算，不人为分配固定配额。"""

    physical_window: int | None = None
    output_reserve: int = 16384
    headroom_tokens: int | None = None
    trigger_ratio: float = 0.95
    rollover_token_limit: int = 0
    automatic_rollover: bool = True
    evidence_token_budget: int | None = None
    output_limit_supported: bool = True
    working_set_token_budget: int | None = None

    def __post_init__(self) -> None:
        if self.output_reserve < 0 or not 0 < self.trigger_ratio <= 1:
            raise ValueError("Invalid output reserve or rollover trigger ratio")
        if self.headroom_tokens is not None and self.headroom_tokens < 0:
            raise ValueError("Context headroom must be nonnegative")
        if self.evidence_token_budget is not None and self.evidence_token_budget < 0:
            raise ValueError("Evidence token budget must be nonnegative")
        if (
            self.working_set_token_budget is not None
            and self.working_set_token_budget < 0
        ):
            raise ValueError("Working-set token budget must be nonnegative")
        if self.physical_window is not None and (
            self.physical_window <= self.output_reserve + self.headroom
        ):
            raise ValueError("Output reserve and headroom leave no input budget")

    @property
    def headroom(self) -> int:
        """默认把旧比例阈值留下的空间拆成输出预留与额外余量，避免重复扣减。"""
        if self.headroom_tokens is not None:
            return self.headroom_tokens
        if self.physical_window is None:
            return 0
        ratio_reserve = self.physical_window - int(
            self.physical_window * self.trigger_ratio
        )
        return max(0, ratio_reserve - self.output_reserve)

    @property
    def input_budget(self) -> int:
        if self.physical_window is None:
            return 0
        return self.physical_window - self.output_reserve - self.headroom

    @property
    def rotation_threshold(self) -> int:
        threshold = self.input_budget or 32000
        if self.physical_window is not None:
            threshold = min(threshold, int(self.physical_window * self.trigger_ratio))
        if self.rollover_token_limit > 0:
            threshold = min(threshold, self.rollover_token_limit)
        return max(1, threshold)

    @property
    def evidence_budget(self) -> int:
        if self.evidence_token_budget is not None:
            return self.evidence_token_budget
        return min(32000, self.input_budget or 32000)

    @property
    def working_budget(self) -> int:
        """默认只带走一个有界的近期交互，给规则与任务状态留下空间。"""
        if self.working_set_token_budget is not None:
            return self.working_set_token_budget
        return min(4096, self.input_budget // 4) if self.input_budget else 4096

    def recent_working_set(
        self, messages: list[AgentMessage], protected_ids: frozenset[str] = frozenset()
    ) -> list[AgentMessage]:
        """保留最后一组交互的协议完整性，大正文通过历史引用回收。"""
        group = latest_working_group(messages)
        if not group or self.working_budget <= 0:
            return []
        non_evidence = [m for m in group if not isinstance(m, ToolResultMessage)]
        available = max(0, self.working_budget - estimate_message_tokens(non_evidence))
        projected, _ = self.project_evidence(
            group, protected_ids, token_budget=available
        )
        return (
            projected
            if estimate_message_tokens(projected) <= self.working_budget
            else []
        )

    def can_reclaim_history(
        self, messages: list[AgentMessage], protected_ids: frozenset[str] = frozenset()
    ) -> bool:
        """必需内容已经构成最小工作窗口时，自动换窗不能反复解决同一超限。"""
        retained = {id(m) for m in mandatory_history(messages, protected_ids)}
        if self.recent_working_set(messages, protected_ids):
            retained.update(id(m) for m in latest_working_group(messages))
        return any(id(m) not in retained for m in messages)

    def for_provider(self, provider: StreamProvider) -> ContextPolicy:
        """切换到较小窗口时收紧预算；不扩大调用方显式限制的窗口。"""
        window = getattr(provider, "context_window", None)
        if not isinstance(window, int) or window <= 0:
            window = get_model_context_window(
                str(getattr(provider, "model", "")),
                transport=getattr(provider, "transport", None),
            )
        if window is None:
            window = self.physical_window
        elif self.physical_window is not None:
            window = min(self.physical_window, window)
        return replace(
            self,
            physical_window=window,
            output_limit_supported=bool(
                getattr(provider, "supports_output_token_limit", True)
            ),
        )

    def for_request(self, options: StreamOptions | None) -> ContextPolicy:
        """显式增大输出上限时先扩大预留，再计算输入预算。"""
        if options is None or options.max_tokens is None:
            return self
        if options.max_tokens <= 0:
            raise ValueError("Maximum output tokens must be positive")
        return replace(
            self, output_reserve=max(self.output_reserve, options.max_tokens)
        )

    def request_options(self, options: StreamOptions | None) -> StreamOptions | None:
        """支持上限的 transport 才下发；否则预留只是运行时预算。"""
        if not self.output_limit_supported:
            return replace(options, max_tokens=None) if options is not None else None
        if self.output_reserve <= 0 or (
            options is not None and options.max_tokens is not None
        ):
            return options
        return replace(options or StreamOptions(), max_tokens=self.output_reserve)

    def should_rotate(self, predicted_input: int) -> bool:
        return self.automatic_rollover and predicted_input >= self.rotation_threshold

    def project_working_set(
        self,
        messages: list[AgentMessage],
        protected_ids: frozenset[str],
        reclaim_tokens: int,
    ) -> tuple[list[AgentMessage], tuple[str, ...]]:
        """正文回收后仍不足时，把旧的完整工具交互替换为事实索引。"""
        recent = latest_working_group(messages)
        recent_assistant = recent[0] if recent else None
        removed: set[int] = set()
        references: dict[int, SystemMessage] = {}
        omitted: list[str] = []
        reclaimed = 0
        for index, message in enumerate(messages):
            if reclaimed >= reclaim_tokens:
                break
            if not isinstance(message, AssistantMessage) or message is recent_assistant:
                continue
            calls = [b for b in message.content if isinstance(b, ToolCallContent)]
            ids = {b.id for b in calls}
            if not ids or ids.intersection(protected_ids):
                continue
            result_indices = [
                i
                for i, result in enumerate(messages)
                if i > index
                and isinstance(result, ToolResultMessage)
                and result.tool_call_id in ids
            ]
            results = [messages[i] for i in result_indices]
            if {
                m.tool_call_id for m in results if isinstance(m, ToolResultMessage)
            } != ids:
                continue
            reference = SystemMessage(
                content=(
                    "<context-history-reference>Older completed tool interaction omitted "
                    "from this request. Exact arguments, results and execution status remain "
                    "in history; retrieve by tool_call_id when needed. Calls: "
                    + json.dumps(
                        [{"tool_call_id": b.id, "tool": b.name} for b in calls]
                    )
                    + "</context-history-reference>"
                )
            )
            old_cost = sum(
                estimate_wire_message_tokens(m)
                for m in convert_to_llm([message, *results])
            )
            new_cost = sum(
                estimate_wire_message_tokens(m) for m in convert_to_llm([reference])
            )
            if old_cost <= new_cost:
                continue
            removed.update([index, *result_indices])
            references[index] = reference
            omitted.extend(b.id for b in calls)
            reclaimed += old_cost - new_cost
        projected: list[AgentMessage] = []
        for index, message in enumerate(messages):
            if index in references:
                projected.append(references[index])
            elif index not in removed:
                projected.append(message)
        return projected, tuple(omitted)

    def project_evidence(
        self,
        messages: list[AgentMessage],
        protected_ids: frozenset[str] = frozenset(),
        *,
        token_budget: int | None = None,
    ) -> tuple[list[AgentMessage], tuple[str, ...]]:
        """按新到旧分配工具正文预算，原文和执行状态仍保留在事实历史中。"""
        projected = list(messages)
        remaining = (
            self.evidence_budget if token_budget is None else max(0, token_budget)
        )
        omitted: list[str] = []
        for index in range(len(messages) - 1, -1, -1):
            message = messages[index]
            if (
                not isinstance(message, ToolResultMessage)
                or message.tool_call_id in protected_ids
            ):
                continue
            if isinstance(message.content, str):
                text = message.content
            elif all(isinstance(block, TextContent) for block in message.content):
                text = "\n".join(
                    block.text
                    for block in message.content
                    if isinstance(block, TextContent)
                )
            else:
                # 多模态结果由 provider 计量，不猜测图像或文件的 token 成本。
                continue
            tokens = estimate_tokens(text)
            if tokens <= remaining:
                remaining -= tokens
                continue
            preview = _evidence_preview(text, remaining)
            remaining = (
                max(0, remaining - estimate_tokens(preview)) if preview else remaining
            )
            reference = evidence_reference(message)
            content = f"{reference}\n{preview}" if preview else reference
            # 小结果的原文比引用更省空间，不能越回收越大。
            if estimate_tokens(content) >= tokens:
                remaining = max(0, remaining - tokens)
                continue
            replacement = (
                content
                if isinstance(message.content, str)
                else [TextContent(text=content)]
            )
            projected[index] = message.model_copy(update={"content": replacement})
            omitted.append(message.tool_call_id)
        return projected, tuple(reversed(omitted))


def _evidence_preview(text: str, token_budget: int) -> str:
    """预览只保留短头尾；引用和状态的开销由完整输入预算计量。"""
    width = min(240, len(text) // 2)
    while width > 0 and token_budget > 0:
        preview = f"{text[:width]}\n[... omitted ...]\n{text[-width:]}"
        if estimate_tokens(preview) <= token_budget:
            return preview
        width //= 2
    return ""


def latest_task_message(messages: list[AgentMessage]) -> UserMessage | None:
    """跳过运行时提醒，保留真实用户任务边界。"""
    for message in reversed(messages):
        if not isinstance(message, UserMessage):
            continue
        if isinstance(message.content, str) and message.content.lstrip().startswith(
            ("<reminder>", "<plan-timeout>")
        ):
            continue
        return message
    return None


def latest_working_group(messages: list[AgentMessage]) -> list[AgentMessage]:
    """最后一个用户请求之后的最近 assistant 及其完整工具结果组。"""
    latest_user = latest_task_message(messages)
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if message is latest_user:
            break
        if isinstance(message, AssistantMessage):
            group = list(messages[index:])
            calls = {b.id for b in message.content if isinstance(b, ToolCallContent)}
            results = {
                m.tool_call_id for m in group if isinstance(m, ToolResultMessage)
            }
            return group if calls.issubset(results) else []
    return []


def protected_working_set(
    messages: list[AgentMessage], protected_ids: frozenset[str]
) -> list[AgentMessage]:
    """持久工具状态保留整组调用和结果，避免拆散并行工具协议。"""
    retained: list[AgentMessage] = []
    for index, message in enumerate(messages):
        if not isinstance(message, AssistantMessage):
            continue
        call_ids = {b.id for b in message.content if isinstance(b, ToolCallContent)}
        if not call_ids.intersection(protected_ids):
            continue
        retained.append(message)
        retained.extend(
            m
            for m in messages[index + 1 :]
            if isinstance(m, ToolResultMessage) and m.tool_call_id in call_ids
        )
    return retained


def mandatory_history(
    messages: list[AgentMessage], protected_ids: frozenset[str]
) -> list[AgentMessage]:
    """启动上下文、当前用户意图与持久工具状态构成不可淘汰的历史底线。"""
    retained = {id(m) for m in protected_working_set(messages, protected_ids)}
    latest_user = latest_task_message(messages)
    if latest_user is not None:
        retained.add(id(latest_user))
    for m in messages:
        if not isinstance(m, SystemMessage):
            break
        retained.add(id(m))
    return [m for m in messages if id(m) in retained]


def evidence_reference(message: ToolResultMessage) -> str:
    """所有工具输出裁剪共用可检索的引用格式。"""
    return (
        "[Tool output omitted from this request; exact output remains in history. "
        f"tool_call_id={message.tool_call_id}; tool_name={message.tool_name}; "
        f"is_error={str(message.is_error).lower()}. "
        "Preview is incomplete; recover original commands and evidence from history.]"
    )
