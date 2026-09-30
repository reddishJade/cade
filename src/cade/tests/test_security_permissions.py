"""E2E 无法安全穷举的权限硬边界回归。

需要防止的失效方式是危险命令被误放行、模式绕过审批路由、审批范围升级、
路径逃逸、受保护元数据写入和敏感文件 override 扩大。逐条执行这些场景会
修改真实文件或触发危险命令，所以在权限引擎边界做窄测试。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from cade.agent.types import ApprovalRequest, ToolSpec
from cade.coding_agent.assembly.security import (
    sensitive_path_overrides_from_security,
)
from cade.coding_agent.execution_modes import build_default_mode_rulesets
from cade.harness.config import SecurityRuntimeConfig
from cade.harness.security import (
    ActionExtractor,
    HITLResult,
    ShellAnalysisPolicyEvaluator,
    analyze_shell_command,
)
from cade.harness.security.permission_model import Rule, SensitivePathOverride
from cade.harness.security.permissions import PermissionEngine, PermissionEngineConfig


def _bash() -> ToolSpec:
    return ToolSpec("bash", "", "", lambda _data, _update: "")


def _engine(tmp_path: Path, mode: str, **kwargs: object) -> PermissionEngine:
    return PermissionEngine(
        PermissionEngineConfig(
            project_root=tmp_path,
            mode_ruleset=build_default_mode_rulesets(tmp_path)[mode],
            mode_fallback="deny" if mode == "plan" else "ask",
            shell_unresolved_policy="ask",
            shell_mutation_policy="deny" if mode == "plan" else "ask",
            execution_mode=mode,
            **kwargs,
        )
    )


def test_read_only_command_is_allowed_without_review(tmp_path: Path) -> None:
    result = _engine(tmp_path, "build").decide(
        "bash",
        {"command": "git log --oneline -10"},
        tool_spec=_bash(),
        approval_callback=lambda _request: pytest.fail("unexpected approval"),
    )

    assert result.decision == "allow"
    assert result.blocked is False


def test_mode_controls_who_can_approve_an_unresolved_command(tmp_path: Path) -> None:
    requests: list[ApprovalRequest] = []

    def approve(request: ApprovalRequest) -> HITLResult:
        requests.append(request)
        return HITLResult("allow", "once")

    build_result = _engine(tmp_path, "build").decide(
        "bash",
        {"command": "pytest -q"},
        tool_spec=_bash(),
        approval_callback=approve,
        approvals_reviewer="auto_review",
    )
    act_result = _engine(tmp_path, "act").decide(
        "bash",
        {"command": "pytest -q"},
        tool_spec=_bash(),
        approval_callback=approve,
        approvals_reviewer="user",
    )

    assert build_result.decision == "allow"
    assert build_result.source == "auto_review"
    assert act_result.decision == "allow"
    assert [request.execution_mode for request in requests] == ["build", "act"]


def test_plan_and_dangerous_commands_cannot_be_overridden(tmp_path: Path) -> None:
    reviewer_calls: list[ApprovalRequest] = []

    def approve(request: ApprovalRequest) -> HITLResult:
        reviewer_calls.append(request)
        return HITLResult("allow", "once")

    plan_result = _engine(tmp_path, "plan").decide(
        "bash",
        {"command": "rm -rf build"},
        tool_spec=_bash(),
        approval_callback=approve,
        approvals_reviewer="auto_review",
    )
    dangerous_result = PermissionEngine(
        PermissionEngineConfig(
            mode_ruleset=(Rule(action="bash", effect="allow"),),
            mode_fallback="allow",
            shell_unresolved_policy="allow",
        )
    ).decide(
        "bash",
        {"command": "rm -rf /"},
        tool_spec=_bash(),
        approval_callback=approve,
    )

    assert plan_result.decision == "deny"
    assert dangerous_result.decision == "deny"
    assert reviewer_calls == []


def test_paths_outside_workspace_are_hard_denied(tmp_path: Path) -> None:
    result = _engine(tmp_path, "build").decide(
        "read", {"path": str(tmp_path.parent / "outside.txt")}
    )

    assert result.decision == "deny"
    assert result.reason_code == "outside_approved_roots"
    assert result.overrideable is False


def test_protected_workspace_metadata_cannot_be_written(tmp_path: Path) -> None:
    result = _engine(tmp_path, "build").decide(
        "write", {"path": ".cade/mcp_config.json", "content": "x"}
    )

    assert result.decision == "deny"
    assert result.reason_code == "protected_workspace_metadata"
    assert result.overrideable is False


@pytest.mark.parametrize(
    ("tool", "path", "allowed"),
    [
        ("save_memory", ".cade/memory/timeout.md", True),
        ("save_memory", ".cade/sessions/timeout.md", False),
        ("save_memory", ".cade/memory/timeout.json", False),
        ("save_memory", "nested/.cade/memory/timeout.md", False),
        ("write", ".cade/memory/timeout.md", False),
        ("edit", ".cade/memory/timeout.md", False),
        ("patch", ".cade/memory/timeout.md", False),
    ],
)
def test_memory_write_exception_is_limited_to_save_tool(
    tmp_path: Path, tool: str, path: str, allowed: bool
) -> None:
    """在权限边界穷举例外，避免真实写入受保护的 session 文件。"""
    spec = ToolSpec(tool, "", "", lambda _data, _update: "")
    result = _engine(tmp_path, "build").decide(tool, {"path": path}, tool_spec=spec)
    assert (result.decision == "allow") is allowed


def test_memory_symlink_cannot_redirect_save_into_sessions(tmp_path: Path) -> None:
    """链接别名不得扩大保存能力的目录范围。"""
    sessions = tmp_path / ".cade" / "sessions"
    sessions.mkdir(parents=True)
    (tmp_path / ".cade" / "memory").symlink_to(sessions, target_is_directory=True)
    result = _engine(tmp_path, "build").decide(
        "save_memory", {"path": ".cade/memory/source.md"}
    )
    assert result.decision == "deny"


def test_auto_review_cannot_create_a_session_grant() -> None:
    result = PermissionEngine(
        PermissionEngineConfig(
            mode_ruleset=(Rule(action="bash", effect="ask"),),
            mode_fallback="ask",
        )
    ).decide(
        "bash",
        {"command": "pytest -q"},
        tool_spec=_bash(),
        approval_callback=lambda _request: HITLResult("allow", "session"),
        approvals_reviewer="auto_review",
    )

    assert result.decision == "deny"
    assert result.reason_code == "invalid_auto_review_scope"


def test_unresolved_paths_are_denied_when_restricted_dirs_are_configured() -> None:
    result = PermissionEngine(
        PermissionEngineConfig(restricted_dirs=("secrets",))
    ).decide("read", {})

    assert result.decision == "deny"
    assert result.reason_code == "unresolved_path_with_restricted_dirs"
    assert result.overrideable is False


def test_sensitive_override_is_exact_and_read_only(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    engine = PermissionEngine(
        PermissionEngineConfig(
            project_root=tmp_path,
            sensitive_path_overrides=(
                SensitivePathOverride(path=env_path, access="read"),
            ),
            mode_fallback="allow",
        )
    )

    read_result = engine.decide("read", {"path": str(env_path)})
    write_result = engine.decide("write", {"path": str(env_path)})
    near_match_result = engine.decide("read", {"path": str(tmp_path / ".env.local")})

    assert read_result.decision == "allow"
    assert write_result.reason_code == "sensitive_path"
    assert near_match_result.reason_code == "sensitive_path"


def test_runtime_sensitive_overrides_require_exact_paths(tmp_path: Path) -> None:
    security = SecurityRuntimeConfig.model_validate(
        {"sensitive_path_overrides": [{"path": ".env", "access": "read"}]}
    )

    assert sensitive_path_overrides_from_security(security, tmp_path) == (
        SensitivePathOverride(path=tmp_path / ".env", access="read"),
    )
    with pytest.raises(ValidationError, match="must be an exact path"):
        SecurityRuntimeConfig.model_validate(
            {"sensitive_path_overrides": [{"path": "**/.env", "access": "read"}]}
        )


def test_safe_search_classification_does_not_hide_external_programs() -> None:
    safe = analyze_shell_command("rg needle src | head -n 20")
    unsafe = analyze_shell_command("rg --pre=helper needle src")

    assert [target.value for target in safe.resolved_paths] == ["src"]
    assert safe.unresolved_effects == ()
    assert [effect.reason for effect in unsafe.unresolved_effects] == [
        "wrapper_command"
    ]


def test_find_output_is_a_write_and_recursive_root_delete_is_denied() -> None:
    output = analyze_shell_command("find . -fprint report.txt")
    action = ActionExtractor().extract(
        "bash",
        {"command": "git -C /tmp/repo clean -fdx"},
        ("shell", "none"),
    )

    constraints = ShellAnalysisPolicyEvaluator().evaluate(action)

    assert [(path.value, path.access) for path in output.resolved_paths] == [
        (".", "read"),
        ("report.txt", "write"),
    ]
    assert [effect.reason for effect in output.unresolved_effects] == ["mutation"]
    assert [constraint.decision for constraint in constraints] == ["deny"]
