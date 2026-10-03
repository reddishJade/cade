from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ContextSummary:
    categories: list[tuple[str, int]] = field(default_factory=list)
    total: int = 0
    context_window: int = 0
    model_name: str = ""
    spent: float = 0.0
    free: int = 0
    skill_count: int = 0
    instruction_files: list[str] = field(default_factory=list)
    skill_source_dirs: list[tuple[str, str]] = field(default_factory=list)
    context_usage: str = ""
    context_cost: str = ""
    usage_stats: str = ""


def _format_token(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def _count_output_tokens(messages: list[object]) -> int:
    """Sum output tokens from AssistantMessage.usage across history."""
    total = 0
    for msg in messages:
        usage = getattr(msg, "usage", None) or {}
        if isinstance(usage, dict):
            ct = usage.get("completion_tokens") or usage.get("output_tokens", 0)
            total += ct if isinstance(ct, int) else 0
        elif hasattr(usage, "output"):
            ct = getattr(usage, "output", 0)
            total += ct if isinstance(ct, int) else 0
    return total


def _count_tokens_by_message_role(messages: list[object]) -> dict[str, int]:
    """按角色拆解消息 token 用量（user / agent / tool_calls）。"""
    from cade.agent._context_window import estimate_tokens
    from cade.agent.messages import (
        AssistantMessage,
        ToolResultMessage,
        UserMessage,
    )
    from cade.agent.types import TextContent, ThinkingContent, ToolCallContent

    result: dict[str, int] = {"user": 0, "agent": 0, "tool_calls": 0}

    for msg in messages:
        if isinstance(msg, UserMessage):
            content = msg.content
            if isinstance(content, str):
                result["user"] += estimate_tokens(content)
            else:
                for block in content:
                    if isinstance(block, TextContent):
                        result["user"] += estimate_tokens(block.text)
        elif isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, (TextContent, ThinkingContent)):
                    text = (
                        block.text if isinstance(block, TextContent) else block.thinking
                    )
                    result["agent"] += estimate_tokens(text)
                elif isinstance(block, ToolCallContent):
                    import json

                    result["tool_calls"] += estimate_tokens(
                        json.dumps(block.arguments or {}, default=str)
                    )
            if msg.reasoning_content:
                result["agent"] += estimate_tokens(msg.reasoning_content)
        elif isinstance(msg, ToolResultMessage):
            content = msg.content
            if isinstance(content, str):
                result["tool_calls"] += estimate_tokens(content)
            else:
                for block in content:
                    if isinstance(block, TextContent):
                        result["tool_calls"] += estimate_tokens(block.text)

    return result


def _get_context_window(
    model_name: str, context_window_override: int | None = None
) -> int:
    """返回模型上下文窗口；优先使用 provider profile 的覆盖值。"""
    if context_window_override is not None and context_window_override > 0:
        return context_window_override
    from cade.ai.models import get_models, get_providers

    for provider in get_providers():
        for model in get_models(provider):
            if model.id == model_name:
                return model.context_window
    return 0


def _get_model_cost(model_name: str) -> object | None:
    from cade.ai.models import get_model_cost as _resolve_model_cost

    return _resolve_model_cost(model_name)


def _usage_stats_for_agent(agent: object) -> str:
    """从 provider 累计用量生成底栏摘要；无 usage 记录时返回空串。"""
    from cade.ai.usage import format_usage_stats

    provider = getattr(agent, "provider", None)
    totals = getattr(provider, "usage_totals", None)
    if totals is None or totals.requests == 0:
        return ""
    hit_rate = getattr(provider, "cache_hit_rate", None)
    return format_usage_stats(totals, hit_rate)


def compute_context_summary(agent: object, project_root: Path) -> ContextSummary:
    """计算上下文统计并返回宿主可消费的快照。"""
    from cade.agent._context_window import estimate_tokens
    from cade.coding_agent.prompting.identity import (
        CORE_IDENTITY,
    )
    from cade.harness.agent_runtime.prompting.identity import (
        SEARCH_STRATEGY,
        TOOL_DISCIPLINE,
    )

    categories: list[tuple[str, int]] = []

    system_text = f"{CORE_IDENTITY}\n\n{TOOL_DISCIPLINE}\n\n{SEARCH_STRATEGY}"
    categories.append(("System prompt", estimate_tokens(system_text)))

    registry = getattr(agent, "registry", None)
    if registry is not None:
        snap = registry
        from cade.harness.agent_runtime.prompting import (
            build_tool_guidelines,
            build_tool_prompt,
        )

        parts = ["Available tools:\n" + build_tool_prompt(snap)]
        guidelines = build_tool_guidelines(snap)
        if guidelines:
            parts.append("Guidelines:\n" + guidelines)
        categories.append(("System tools", estimate_tokens("\n\n".join(parts))))

    history_messages = getattr(agent, "history_messages", None)
    messages = history_messages() if history_messages is not None else []
    role_counts = _count_tokens_by_message_role(messages)
    for key, label in [
        ("user", "User messages"),
        ("agent", "Agent responses"),
        ("tool_calls", "Tool calls"),
    ]:
        if tokens := role_counts.get(key, 0):
            categories.append((label, tokens))

    skill_count = 0
    runtime = getattr(agent, "_runtime", None)
    skill_registry = getattr(runtime, "skill_registry", None) if runtime else None
    if skill_registry is not None and hasattr(skill_registry, "list_summaries"):
        summaries = skill_registry.list_summaries()
        if summaries:
            skill_count = len(summaries)
            lines = [
                (
                    "<skill-activation>\n"
                    "When the user task clearly matches a skill description below, "
                    "call load_skill with that exact name before performing the task. "
                    "Do not load a skill when no description clearly matches.\n"
                    "</skill-activation>"
                ),
                "<available-skills>",
            ]
            for s in summaries:
                desc = s.description
                if len(desc) > 768:
                    desc = desc[:765] + "..."
                lines.append(f"  <skill name={s.name}>{desc}</skill>")
            lines.append("</available-skills>")
            categories.append(("Skills", estimate_tokens("\n".join(lines))))

    instruction_files: list[str] = []
    agents_md = project_root / "AGENTS.md"
    if agents_md.is_file():
        instruction_files.append(str(agents_md))

    skill_source_dirs: list[tuple[str, str]] = []
    if skill_registry is not None and hasattr(skill_registry, "list_summaries"):
        seen_dirs: set[str] = set()
        for skill in skill_registry.list_summaries():
            src = skill.source or "user"
            label = {"explicit": "explicit", "project": "project", "user": "user"}.get(
                src, src
            )
            key = (label, src)
            if key not in seen_dirs and src not in seen_dirs:
                seen_dirs.add(src)
        standard_dirs = []
        from cade.harness.skills.discovery import build_skill_search_dirs

        for path, priority in build_skill_search_dirs(project_root):
            src_label = {
                0: "explicit",
                1: "project",
                2: "project",
                3: "user",
                4: "user",
            }.get(priority, "user")
            if path.is_dir() and src_label in seen_dirs:
                standard_dirs.append((src_label, str(path)))
        skill_source_dirs = standard_dirs

    provider = getattr(agent, "provider", None)
    inner = getattr(provider, "active_provider", provider)
    model_name = getattr(inner, "model", "unknown") if inner else "unknown"
    context_window = _get_context_window(
        model_name, getattr(inner, "context_window", None)
    )
    cost = _get_model_cost(model_name)

    total = sum(t for _, t in categories)
    free = max(0, context_window - total) if context_window > 0 else 0
    cost_input_rate = getattr(cost, "input", 0) if cost else 0
    cost_output_rate = getattr(cost, "output", 0) if cost else 0

    input_cost = (total / 1_000_000) * cost_input_rate if cost_input_rate else 0
    history = getattr(agent, "history_messages", list)()
    output_tokens = _count_output_tokens(history)
    output_cost = (
        (output_tokens / 1_000_000) * cost_output_rate if cost_output_rate else 0
    )
    spent = input_cost + output_cost

    context_str = (
        f"{_format_token(total)}/{_format_token(context_window)}"
        f" ({total / context_window * 100:.1f}%)"
        if context_window > 0
        else f"{_format_token(total)} tokens"
    )
    cost_str = f"${spent:.2f}" if spent > 0 else ""

    return ContextSummary(
        categories=categories,
        total=total,
        context_window=context_window,
        model_name=model_name,
        spent=spent,
        free=free,
        skill_count=skill_count,
        instruction_files=instruction_files,
        skill_source_dirs=skill_source_dirs,
        context_usage=context_str,
        context_cost=cost_str,
        usage_stats=_usage_stats_for_agent(agent),
    )
