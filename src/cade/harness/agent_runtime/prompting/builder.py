"""System prompt 构建器：agent 身份、工具纪律与环境快照。

本模块构建运行时前缀；动态 section 由 agent/context.py 收集。
职责边界：
- 固定文本：agent 身份、工具纪律、搜索策略与环境信息（装配时生成）
- 操作指南：从当次启用的工具集合生成
- 当前目录：由独立动态 section 提供，不改写系统前缀

不属于系统前缀（由动态 section 收集器管理）：
- 项目指令 → InstructionCollector
- 验证事实 → coding_agent.validation.ValidationCollector
- 笔记文件 → NotesCollector
- 技能摘要 → SkillIndexCollector
"""

from __future__ import annotations

import platform
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from cade.agent.context import (
    ContextBlock,
    ContextBlockSource,
    ContextBlockTarget,
    ContextCollectionInput,
    ContextPriority,
    ContextSection,
)
from cade.agent.types import ToolSpec
from cade.harness.config import DEFAULT_PROMPT_MODULES

from .identity import (
    CITATION_INSTRUCTION,
    DYNAMIC_PROMPT_MODULE_ORDER,
    SEARCH_STRATEGY,
    STABLE_PROMPT_MODULE_ORDER,
    SYSTEM_PROMPT_DYNAMIC_BOUNDARY,
    TOOL_DISCIPLINE,
)
from .tools import build_tool_guidelines

_MAX_CWD_ENTRIES = 12


class ShellInfo(Protocol):
    """环境提示对 shell 规格的最小依赖。"""

    @property
    def name(self) -> str: ...

    @property
    def syntax(self) -> str: ...


def _stable_prompt(enabled: set[str], identity: str) -> str:
    """在装配时生成固定的身份与规则。"""
    parts: list[str] = []
    for module in STABLE_PROMPT_MODULE_ORDER:
        if module == "tools" or module not in enabled:
            continue
        match module:
            case "identity":
                if identity:
                    parts.append(identity)
            case "tool_discipline":
                parts.append(TOOL_DISCIPLINE)
            case "citations":
                parts.append(CITATION_INSTRUCTION)
            case "search_strategy":
                parts.append(SEARCH_STRATEGY)
    return "\n\n".join(part for part in parts if part)


def build_runtime_context_provider(
    project_root: Path,
    modules: tuple[str, ...] | None = None,
    shell_spec: ShellInfo | None = None,
    identity: str = "",
) -> Callable[[tuple[ToolSpec, ...]], list[str]]:
    """装配固定前缀；可变目录清单由独立事实 section 收集。"""
    root = project_root.resolve()
    enabled = set(modules or DEFAULT_PROMPT_MODULES)
    stable_prompt = _stable_prompt(enabled, identity)
    environment = (
        _environment_info(root, shell_spec) if "environment" in enabled else ""
    )

    def provide(registry: tuple[ToolSpec, ...]) -> list[str]:
        dynamic_parts: list[str] = []
        for module in DYNAMIC_PROMPT_MODULE_ORDER:
            if module not in enabled:
                continue
            match module:
                case "environment":
                    dynamic_parts.append(environment)
        dynamic_prompt = "\n\n".join(dynamic_parts)
        full_parts: list[str] = []
        if stable_prompt.strip():
            full_parts.append(stable_prompt)
        guidelines = build_tool_guidelines(registry) if "tools" in enabled else ""
        if guidelines:
            full_parts.append("Guidelines:\n" + guidelines)
        if full_parts and dynamic_prompt.strip():
            full_parts.append(SYSTEM_PROMPT_DYNAMIC_BOUNDARY)
        if dynamic_prompt.strip():
            full_parts.append(dynamic_prompt)
        parts = ["\n\n".join(part for part in full_parts if part.strip())]
        parts.append(
            "NOTE.md owns current task state. Keep status, next action, constraints and "
            "verification evidence first and concise; only its first 4 KiB is injected. "
            "Update it after meaningful edits/verification and before context rollover. "
            "On recovery, reconcile it with current code and history, then continue "
            "unfinished work; a completed task should be reported as complete."
        )
        return parts

    return provide


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


def make_directory_context_section(project_root: Path) -> ContextSection:
    """把当前目录作为请求末尾的事实快照，避免改变已缓存的系统前缀。"""
    root = project_root.resolve()

    def collect(input: ContextCollectionInput) -> list[ContextBlock]:
        return [
            ContextBlock(
                source=ContextBlockSource.ENVIRONMENT,
                priority=ContextPriority.CONTEXT,
                target=ContextBlockTarget.USER_CONTEXT,
                content=_cwd_info(root),
                block_id="working_directory",
                provenance=str(root),
                scope="runtime",
            )
        ]

    return ContextSection("cwd", collect)


def _cwd_info(project_root: Path) -> str:
    names = _cwd_entries(project_root)
    return "<cwd-info>\n" + "\n".join(names) + "\n</cwd-info>"


def _cwd_entries(project_root: Path) -> tuple[str, ...]:
    names = []
    try:
        entries = sorted(project_root.iterdir())
    except OSError:
        return ()
    for path in entries:
        if path.name in {".git", ".venv", "__pycache__"}:
            continue
        names.append(path.name + ("/" if path.is_dir() else ""))
        if len(names) >= _MAX_CWD_ENTRIES:
            break
    return tuple(names)
