from __future__ import annotations

from dataclasses import dataclass, field

from cade.agent.context_policy import ContextSnapshot
from cade.ai.usage import format_usage_stats

from .app_contract import InteractionAgent


@dataclass
class ContextSummary:
    snapshot: ContextSnapshot | None = None
    model_name: str = ""
    instruction_files: list[str] = field(default_factory=list)
    skill_count: int = 0
    skill_tokens: int = 0
    usage_stats: str = ""

    @property
    def context_usage(self) -> str:
        """显示请求输入预测，明确区分本地与实测校准估算。"""
        snapshot = self.snapshot
        if snapshot is None:
            return ""
        total = snapshot.total_input_tokens
        if snapshot.physical_window:
            return (
                f"{_format_token(total)}/{_format_token(snapshot.physical_window)}"
                f" ({total / snapshot.physical_window * 100:.1f}%)"
            )
        return f"{_format_token(total)} tokens"


def _format_token(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def compute_context_summary(agent: InteractionAgent) -> ContextSummary:
    """直接消费当前请求投影与 provider 累计用量，不另建计量机制。"""
    assembly = agent.preview_request()
    provider = agent.provider
    totals = provider.usage_totals
    instructions = list(
        dict.fromkeys(
            trace.provenance
            for trace in assembly.context_trace
            if trace.source == "instruction"
            and trace.included
            and trace.provenance
            and trace.provenance != "inline instruction"
        )
    )
    return ContextSummary(
        snapshot=assembly.context_snapshot,
        model_name=provider.model,
        instruction_files=instructions,
        skill_count=len(agent.available_skill_names()),
        skill_tokens=sum(
            trace.token_count
            for trace in assembly.context_trace
            if trace.source == "skill" and trace.included
        ),
        usage_stats=(
            format_usage_stats(totals, provider.cache_hit_rate)
            if totals.requests
            else ""
        ),
    )
