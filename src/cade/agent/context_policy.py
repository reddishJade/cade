"""请求预算策略：输出预留、安全余量与工作窗口共用一个边界。"""

from __future__ import annotations

from dataclasses import dataclass, replace

from cade.ai.models import get_model_context_window
from cade.ai.providers.base import StreamProvider
from cade.ai.types import StreamOptions

from ._context_window import estimate_tokens
from .messages import AgentMessage, ToolResultMessage
from .types import TextContent


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

    def __post_init__(self) -> None:
        if self.output_reserve < 0 or not 0 < self.trigger_ratio <= 1:
            raise ValueError("Invalid output reserve or rollover trigger ratio")
        if self.headroom_tokens is not None and self.headroom_tokens < 0:
            raise ValueError("Context headroom must be nonnegative")
        if self.evidence_token_budget is not None and self.evidence_token_budget < 0:
            raise ValueError("Evidence token budget must be nonnegative")
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
        return min(32000, max(256, (self.input_budget or 96000) // 3))

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

    def project_evidence(
        self,
        messages: list[AgentMessage],
        protected_ids: frozenset[str] = frozenset(),
    ) -> tuple[list[AgentMessage], tuple[str, ...]]:
        """按新到旧分配工具正文预算，原文和执行状态仍保留在事实历史中。"""
        projected = list(messages)
        remaining = self.evidence_budget
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
            reference = (
                "[Tool output omitted from this request; exact output remains in history. "
                f"tool_call_id={message.tool_call_id}; tool_name={message.tool_name}; "
                f"is_error={str(message.is_error).lower()}. "
                "Preview is incomplete; recover original commands and evidence from history.]"
            )
            content = f"{reference}\n{preview}" if preview else reference
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
