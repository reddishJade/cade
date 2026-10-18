"""Plan / Build / Act 的工具可见性策略与三态 ruleset 初始化。

Plan 保留完整只读探索能力和 bash，仅允许结构化写入计划文件。
Build/Act 默认使用最小 coding surface；权限层决定 shell 与写入是否审批。
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from cade.agent.types import ToolSpec
from cade.ai.events import ToolCall
from cade.harness.security.approval import ApprovalsReviewer
from cade.harness.security.permission_model import Rule
from cade.harness.security.permissions import PermissionDecision

ExecutionMode = Literal["plan", "build", "act"]

# 结构化搜索保持可选；默认编码工具通过 bash 组合 rg/find/ls。
_STRUCTURED_SEARCH_TOOLS = frozenset({"glob", "find", "ls", "grep"})

_PLAN_TOOLS = frozenset(
    {
        "read",
        "write",
        "edit",
        "bash",
        "webfetch",
        "websearch",
        "question",
        "history",
        "rollover",
    }
)


class ExecutionModeState:
    """管理显式执行模式、可见工具与审批路由。"""

    def __init__(
        self,
        initial_mode: ExecutionMode = "act",
        approval_router: Literal["mode", "user", "auto"] = "mode",
    ) -> None:
        self._current_mode: ExecutionMode = initial_mode
        self._approval_router = approval_router

    @property
    def current_mode(self) -> ExecutionMode:
        return self._current_mode

    @property
    def approvals_reviewer(self) -> ApprovalsReviewer:
        """Build 使用自动 reviewer；approval_router 可强制固定路由。"""
        if self._approval_router == "auto":
            return "auto_review"
        if self._approval_router == "user":
            return "user"
        return "auto_review" if self._current_mode in {"plan", "build"} else "user"

    def set_mode(self, mode: ExecutionMode) -> None:
        """设置当前执行模式。"""
        self._current_mode = mode

    def check_call(self, call: ToolCall) -> PermissionDecision:
        # 实际动作裁决由 gate 的 ruleset、路径边界与 shell analyzer 执行。
        return "allow"

    def filter_tools(self, registry: tuple[ToolSpec, ...]) -> tuple[ToolSpec, ...]:
        """根据当前模式过滤工具集。"""
        if self._current_mode == "plan":
            return tuple(tool for tool in registry if tool.name in _PLAN_TOOLS)
        return tuple(
            tool for tool in registry if tool.name not in _STRUCTURED_SEARCH_TOOLS
        )


def parse_execution_mode(value: object) -> ExecutionMode | None:
    if not isinstance(value, str):
        return None
    match value:
        case "plan":
            return "plan"
        case "build":
            return "build"
        case "act":
            return "act"
        case _:
            return None


def mode_notice(mode: str) -> str:
    if mode == "plan":
        return (
            '<execution-mode name="plan">\n'
            "Plan Mode is active. Inspect and produce an action plan only. "
            "Do not modify project code. Bash is available for exploration: "
            "known read-only commands run directly, statically known mutations "
            "are blocked, and commands with unresolved effects are reviewed "
            "automatically. You may create or update plan notes under "
            ".cade/plans/*.md.\n"
            "</execution-mode>"
        )
    if mode == "build":
        return (
            '<execution-mode name="build">\n'
            "Build Mode is active. Structured project writes and proven read-only "
            "shell commands run directly; shell commands with unresolved or mutating "
            "effects are reviewed automatically. Hard safety boundaries always "
            "apply.\n"
            "</execution-mode>"
        )
    if mode == "act":
        return (
            '<execution-mode name="act">\n'
            "Act Mode is active. Read tools and proven read-only shell commands "
            "run directly; structured writes and shell commands with unresolved or "
            "mutating effects require user approval.\n"
            "</execution-mode>"
        )
    return ""


def build_default_mode_rulesets(
    project_root: Path | None = None,
) -> dict[str, tuple[Rule, ...]]:
    """构建 coding product 的默认执行模式规则。"""
    read_rules = (
        Rule(action="read", effect="allow"),
        Rule(action="glob", effect="allow"),
        Rule(action="grep", effect="allow"),
        Rule(action="find", effect="allow"),
        Rule(action="ls", effect="allow"),
        Rule(action="webfetch", effect="allow"),
        Rule(action="websearch", effect="allow"),
        Rule(action="question", effect="allow"),
        Rule(action="load_skill", effect="allow"),
        Rule(action="delegate", effect="allow"),
        Rule(action="history", effect="allow"),
        Rule(action="rollover", effect="allow"),
    )
    write_rules = (
        Rule(action="write", effect="allow"),
        Rule(action="edit", effect="allow"),
        Rule(action="save_memory", effect="allow"),
    )
    ask_write_rules = tuple(
        Rule(action=rule.action, effect="ask") for rule in write_rules
    )
    allow_shell_rules = (Rule(action="bash", effect="allow"),)

    plan_rules = read_rules + (
        Rule(action="bash", effect="allow"),
        Rule(
            action="write",
            effect="allow",
            resource_pattern=".cade/plans/*.md",
        ),
        Rule(
            action="edit",
            effect="allow",
            resource_pattern=".cade/plans/*.md",
        ),
        Rule(action="write", effect="allow", resource_pattern="NOTE.md"),
        Rule(action="edit", effect="allow", resource_pattern="NOTE.md"),
    )
    if project_root is not None:
        plan_pattern = (project_root.resolve() / ".cade" / "plans" / "*.md").as_posix()
        plan_rules += (
            Rule(
                action="write",
                effect="allow",
                resource_pattern=plan_pattern,
            ),
            Rule(
                action="edit",
                effect="allow",
                resource_pattern=plan_pattern,
            ),
            Rule(
                action="write",
                effect="allow",
                resource_pattern=(project_root.resolve() / "NOTE.md").as_posix(),
            ),
            Rule(
                action="edit",
                effect="allow",
                resource_pattern=(project_root.resolve() / "NOTE.md").as_posix(),
            ),
        )
    return {
        "plan": plan_rules,
        # 已确认只读 shell 直接执行；未知或有副作用的 shell 由 analyzer 产生 ask。
        "build": read_rules + write_rules + allow_shell_rules,
        # Act 的结构化写入仍 ask；shell 只有未解析/有副作用时进入用户审批。
        "act": read_rules + ask_write_rules + allow_shell_rules,
    }


DEFAULT_MODE_FALLBACKS: dict[str, PermissionDecision] = {
    "plan": "deny",
    "build": "ask",
    "act": "ask",
}


DEFAULT_SHELL_UNRESOLVED_POLICIES: dict[str, PermissionDecision] = {
    "plan": "ask",
    "build": "ask",
    "act": "ask",
}

DEFAULT_SHELL_MUTATION_POLICIES: dict[str, PermissionDecision] = {
    "plan": "deny",
    "build": "ask",
    "act": "ask",
}
