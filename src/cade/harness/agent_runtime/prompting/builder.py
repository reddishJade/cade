"""System prompt 构建器：agent 身份、工具纪律与环境快照。

本模块是 prompt 构建的两个系统之一（另一个见 agent/context_collector.py）。
职责边界：
- 稳定区：agent 身份、工具纪律、工具列表、搜索策略（注册表不变时缓存）
- 动态区：环境信息（OS、Python、CWD）、CWD 目录快照（CWD 不变时缓存）
- 易变区：contextual retrieval 状态、session 通知（每轮重建）

不属于本模块（由 context_collector 管理）：
- 项目指令 → InstructionCollector
- 验证失败 → RecentValidationCollector
- 笔记文件 → NotesCollector
- 技能摘要 → SkillIndexCollector
"""

from __future__ import annotations

import platform
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from cade.agent.types import ToolSpec
from cade.harness.config import DEFAULT_PROMPT_MODULES

from ..contextual import ContextualRetrievalState
from .identity import (
    CITATION_INSTRUCTION,
    DYNAMIC_PROMPT_MODULE_ORDER,
    SEARCH_STRATEGY,
    STABLE_PROMPT_MODULE_ORDER,
    SYSTEM_PROMPT_DYNAMIC_BOUNDARY,
    TOOL_DISCIPLINE,
    VOLATILE_PROMPT_MODULE_ORDER,
)
from .token_budget import MAX_CWD_ENTRIES
from .tools import build_tool_guidelines, build_tool_prompt

if TYPE_CHECKING:
    from cade.harness.memory import MemoryManager

type PromptCacheKey = tuple[object, ...]


class ShellInfo(Protocol):
    """环境提示对 shell 规格的最小依赖。"""

    @property
    def name(self) -> str: ...

    @property
    def syntax(self) -> str: ...


@dataclass(frozen=True)
class PromptContext:
    project_root: Path
    registry: tuple[ToolSpec, ...]
    question: str
    resumed_notice: str | None = None
    interrupted_notice: str | None = None
    contextual_state: ContextualRetrievalState | None = None
    shell_spec: ShellInfo | None = None
    modules: tuple[str, ...] = DEFAULT_PROMPT_MODULES
    identity: str = ""


class SystemPromptBuilder:
    def __init__(self) -> None:
        self._stable_builder = StableRegionBuilder()
        self._dynamic_builder = DynamicRegionBuilder()
        self._volatile_builder = VolatileRegionBuilder()

    def build(self, context: PromptContext) -> str:
        enabled = set(context.modules)
        stable_prompt = self._stable_builder.build(context, enabled)
        dynamic_prompt = self._dynamic_builder.build(context, enabled)
        volatile_parts = self._volatile_builder.build(context, enabled)

        full_parts = []
        if stable_prompt.strip():
            full_parts.append(stable_prompt)
            if dynamic_prompt.strip() or volatile_parts:
                full_parts.append(SYSTEM_PROMPT_DYNAMIC_BOUNDARY)
        if dynamic_prompt.strip():
            full_parts.append(dynamic_prompt)
        if volatile_parts:
            full_parts.append("\n\n".join(volatile_parts))

        return "\n\n".join(part for part in full_parts if part.strip())


class StableRegionBuilder:
    def __init__(self) -> None:
        self._invariant_cache: str | None = None
        self._invariant_key: frozenset | None = None
        self._full_cache: str | None = None
        self._full_key: PromptCacheKey | None = None
        self._tool_prompt_cache: str | None = None
        self._tool_prompt_key: PromptCacheKey | None = None

    def build(self, context: PromptContext, enabled: set[str]) -> str:
        stable_enabled = enabled.intersection(STABLE_PROMPT_MODULE_ORDER)
        registry_key = _registry_prompt_key(context.registry)

        # 不变模块（identity、tool_discipline、citations、search_strategy）
        # 不依赖工具注册表，可独立缓存
        invariant_enabled = stable_enabled - {"tools"}
        inv_key = frozenset(invariant_enabled)
        if self._invariant_cache is None or self._invariant_key != inv_key:
            inv_parts: list[str] = []
            for module in STABLE_PROMPT_MODULE_ORDER:
                if module not in invariant_enabled:
                    continue
                match module:
                    case "identity":
                        if context.identity:
                            inv_parts.append(context.identity)
                    case "tool_discipline":
                        inv_parts.append(TOOL_DISCIPLINE)
                    case "citations":
                        inv_parts.append(CITATION_INSTRUCTION)
                    case "search_strategy":
                        inv_parts.append(SEARCH_STRATEGY)
            self._invariant_cache = "\n\n".join(inv_parts) if inv_parts else ""
            self._invariant_key = inv_key

        # tools 模块依赖注册表
        tool_section = ""
        if "tools" in stable_enabled:
            tool_section = self._tool_prompt_section(context.registry, registry_key)

        # 组合完整 stable prompt
        parts = [p for p in [self._invariant_cache, tool_section] if p]
        full = "\n\n".join(parts)

        # 缓存完整结果，方便外部命中
        full_key = (registry_key, frozenset(stable_enabled))
        if self._full_cache is None or self._full_key != full_key:
            self._full_cache = full
            self._full_key = full_key

        return full

    def _tool_prompt_section(
        self, registry: tuple[ToolSpec, ...], registry_key: PromptCacheKey
    ) -> str:
        if (
            self._tool_prompt_cache is not None
            and self._tool_prompt_key == registry_key
        ):
            return self._tool_prompt_cache
        prompt = _tool_prompt_section(registry)
        self._tool_prompt_key = registry_key
        self._tool_prompt_cache = prompt
        return prompt


class DynamicRegionBuilder:
    def __init__(self) -> None:
        self._dynamic_cache: str | None = None
        self._dynamic_key: PromptCacheKey | None = None

    def build(self, context: PromptContext, enabled: set[str]) -> str:
        dynamic_enabled = enabled.intersection(DYNAMIC_PROMPT_MODULE_ORDER)
        cwd_signature = (
            _cwd_signature(context.project_root) if "cwd" in dynamic_enabled else ()
        )
        dynamic_key = (
            context.project_root,
            context.shell_spec.name if context.shell_spec else None,
            cwd_signature,
            frozenset(dynamic_enabled),
        )

        if self._dynamic_cache is not None and self._dynamic_key == dynamic_key:
            return self._dynamic_cache

        dynamic_parts: list[str] = []
        for module in DYNAMIC_PROMPT_MODULE_ORDER:
            if module not in enabled:
                continue
            match module:
                case "environment":
                    dynamic_parts.append(
                        _environment_info(context.project_root, context.shell_spec)
                    )
                case "cwd":
                    dynamic_parts.append(_cwd_info(context.project_root))

        dynamic_prompt = "\n\n".join(dynamic_parts)
        self._dynamic_cache = dynamic_prompt
        self._dynamic_key = dynamic_key
        return dynamic_prompt


class VolatileRegionBuilder:
    def build(self, context: PromptContext, enabled: set[str]) -> list[str]:
        volatile_parts: list[str] = []
        for module in VOLATILE_PROMPT_MODULE_ORDER:
            if module not in enabled:
                continue
            match module:
                case "contextual_retrieval":
                    if context.contextual_state is None:
                        continue
                    rendered = context.contextual_state.render()
                    if rendered.strip():
                        volatile_parts.append(rendered)
                case "notices":
                    notices = [
                        context.resumed_notice,
                        context.interrupted_notice,
                    ]
                    notice_text = "\n".join(notice for notice in notices if notice)
                    if notice_text:
                        volatile_parts.append(
                            "<session-notices>\n" + notice_text + "\n</session-notices>"
                        )

        return volatile_parts


def _registry_prompt_key(registry: tuple[ToolSpec, ...]) -> PromptCacheKey:
    return tuple(
        (
            tool.name,
            tool.description,
            tool.input_hint,
            tool.prompt_snippet,
            tool.prompt_guidelines,
        )
        for tool in registry
    )


def _tool_prompt_section(registry: tuple[ToolSpec, ...]) -> str:
    parts = ["Available tools:\n" + build_tool_prompt(registry)]
    guidelines = build_tool_guidelines(registry)
    if guidelines:
        parts.append("Guidelines:\n" + guidelines)
    return "\n\n".join(parts)


def build_runtime_context_provider(
    project_root: Path,
    registry: tuple[ToolSpec, ...],
    prompt_builder: SystemPromptBuilder | None = None,
    resumed_notice: Callable[[], str | None] | None = None,
    interrupted_notice: Callable[[], str | None] | None = None,
    contextual_state: ContextualRetrievalState | None = None,
    modules: tuple[str, ...] | None = None,
    shell_spec: ShellInfo | None = None,
    memory_manager: MemoryManager | None = None,
    identity: str = "",
) -> Callable[[str], list[str]]:
    """构建每轮运行时上下文和稳定的长任务记忆协议。"""
    builder = prompt_builder or SystemPromptBuilder()
    root = project_root.resolve()

    def provide(question: str) -> list[str]:
        current_registry = registry
        parts = [
            builder.build(
                PromptContext(
                    project_root=root,
                    registry=current_registry,
                    question=question,
                    resumed_notice=resumed_notice() if resumed_notice else None,
                    interrupted_notice=interrupted_notice()
                    if interrupted_notice
                    else None,
                    contextual_state=contextual_state,
                    modules=modules
                    or PromptContext(
                        project_root=root, registry=(), question=""
                    ).modules,
                    shell_spec=shell_spec,
                    identity=identity,
                )
            )
        ]
        if memory_manager is not None:
            parts.append(render_memory_protocol(memory_manager))
        return parts

    return provide


def render_memory_protocol(manager: MemoryManager) -> str:
    """告诉 Agent 如何使用长期记忆，不在每轮自动塞入检索结果。"""
    return "\n".join(
        (
            "<long-horizon-memory>",
            "NOTE.md is the explicit working-state index for the current task.",
            (
                "Keep its execution frontier first and concise: current status, next "
                "action, user constraints, completed work, remaining work, and "
                "verification evidence (commands, results, relevant files). Only the "
                "first 4 KiB is injected; replace obsolete status instead of appending "
                "another investigation diary."
            ),
            (
                "Update that frontier after meaningful edits or verification, and "
                "before further investigation when a context-budget reminder arrives. "
                "Create a minimal checkpoint before broad investigation; unknown "
                "progress can be recorded as unknown. "
                "On recovery, continue the next unfinished action. Reuse verified "
                "results when relevant files are unchanged; repeat checks only for "
                "changed code or unresolved uncertainty. If the original task is "
                "already complete, report completion instead of starting over."
            ),
            "The lossless session transcript is the source of truth for exact history.",
            f"Project memory: {manager.memory_file}",
            f"User memory: {manager.user_memory_file}",
            "Use recall before asking the user to repeat prior decisions.",
            (
                "Use history list_windows/search/read/around for exact details from "
                "older context windows."
            ),
            (
                "Persist durable user rules, decisions with rationale, verified facts, "
                "or coding experience that was expensive to learn. Keep one "
                "authoritative copy: project experience in project memory, "
                "cross-project personal preferences in user memory. Current task "
                "progress belongs in NOTE.md; exact trajectories belong in history."
            ),
            (
                "Recall experience only when a concrete file, symbol, or error suggests "
                "prior investigation may help; do not search on every task by default. "
                "Experience is a historical hint: current files, git and tests take "
                "precedence. Check anchors and applicability on the current branch "
                "before adopting a fix. Reject a mismatching experience. If the "
                "result was truncated, read the complete record before using it."
            ),
            (
                "After verifying an expensive root cause and fix, you may visibly "
                "save a reusable lesson using ordinary write/edit tools under their "
                "existing permissions. Explain what is being saved. Use one H2 block "
                "with plain labels: Type: experience, Problem:, Root cause:, "
                "Fix pattern:, Applies when:, Anchors:, Evidence:. Put concrete "
                "literal anchors on one line, separated by semicolons. Include real "
                "commit or session/event pointers and the validation command/result; "
                "never invent evidence IDs. Preserve causal detail and boundaries, "
                "without transcripts, slogans, cheap code facts or task summaries. "
                "Update or delete an invalid record explicitly. Do not automatically "
                "promote experience into a skill."
            ),
            "</long-horizon-memory>",
        )
    )


def render_memory_overview(
    manager: MemoryManager,
    max_tokens: int = 6000,
) -> str:
    """渲染预算控制的记忆概览，用于恢复会话时注入。"""
    packets = manager.read_budgeted(max_tokens=max_tokens, layer="all")
    if not packets:
        return ""
    lines = [
        "<memory-overview>",
        "Cross-session project memory. These are prior learnings and decisions",
        "from previous sessions. Treat them as background context.",
    ]
    lines.extend(packets)
    lines.append("</memory-overview>")
    return "\n".join(lines)


def _environment_info(project_root: Path, shell_spec: ShellInfo | None = None) -> str:
    lines = [
        "<environment>",
        f"os={platform.system()} {platform.release()}",
        f"python={platform.python_version()}",
        f"cwd={project_root.resolve()}",
    ]
    if shell_spec is not None:
        lines.append(
            f'<shell tool="bash" name="{shell_spec.name}" syntax="{shell_spec.syntax}" />'
        )
        lines.append(
            "When using the bash tool, write commands for the shell named in <shell> above. "
            "Do not probe for shell availability unless asked."
        )
    lines.append("</environment>")
    return "\n".join(lines)


def _cwd_info(project_root: Path) -> str:
    names = list(_cwd_signature(project_root))
    return "<cwd-info>\n" + "\n".join(names) + "\n</cwd-info>"


def _cwd_signature(project_root: Path) -> tuple[str, ...]:
    names = []
    try:
        entries = sorted(project_root.iterdir())
    except OSError:
        return ()
    for path in entries:
        if path.name in {".git", ".venv", "__pycache__"}:
            continue
        names.append(path.name + ("/" if path.is_dir() else ""))
        if len(names) >= MAX_CWD_ENTRIES:
            break
    return tuple(names)
