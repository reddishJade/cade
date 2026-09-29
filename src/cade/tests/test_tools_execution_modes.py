"""执行模式与策略单元测试。"""

from __future__ import annotations

from pathlib import Path

from cade.agent.types import ApprovalRequest
from cade.coding_agent.execution_modes import (
    DEFAULT_MODE_FALLBACKS,
    DEFAULT_SHELL_UNRESOLVED_POLICIES,
    ActPolicy,
    BuildPolicy,
    ExecutionModeState,
    PlanPolicy,
    build_default_mode_rulesets,
    parse_execution_mode,
)
from cade.harness.agent_runtime.tool_gate import ToolGate
from cade.harness.security import HITLResult


class TestParseExecutionMode:
    def test_none_for_invalid(self) -> None:
        assert parse_execution_mode("unknown") is None

    def test_none_for_non_string(self) -> None:
        assert parse_execution_mode(123) is None


class TestDefaultModeRulesets:
    def test_returns_rules_without_mutating_security_globals(
        self, tmp_path: Path
    ) -> None:
        rulesets = build_default_mode_rulesets(tmp_path)
        assert set(rulesets) == {"plan", "build", "act"}
        assert DEFAULT_MODE_FALLBACKS == {
            "plan": "deny",
            "build": "ask",
            "act": "ask",
        }
        assert DEFAULT_SHELL_UNRESOLVED_POLICIES == {
            "plan": "ask",
            "build": "ask",
            "act": "ask",
        }
        build_shell = next(rule for rule in rulesets["build"] if rule.action == "bash")
        act_shell = next(rule for rule in rulesets["act"] if rule.action == "bash")
        assert build_shell.effect == "allow"
        assert act_shell.effect == "allow"
        plan_shell = next(
            rule
            for rule in rulesets["plan"]
            if rule.action == "bash" and rule.command is None
        )
        plan_rm = next(
            rule
            for rule in rulesets["plan"]
            if rule.action == "bash" and rule.command == "rm"
        )
        assert plan_shell.effect == "allow"
        assert plan_rm.effect == "deny"
        plan_patterns = {
            rule.resource_pattern
            for rule in rulesets["plan"]
            if rule.resource_pattern is not None
        }
        assert (tmp_path / ".cade" / "plans" / "*.md").as_posix() in plan_patterns


def _tool(name: str):
    from cade.agent.types import ToolSpec

    return ToolSpec(name=name, description="", input_hint="", handler=lambda d, _: "")


class TestDefaultCodingSurface:
    def test_build_hides_structured_search_helpers(self) -> None:
        tools = tuple(
            _tool(name)
            for name in (
                "read",
                "write",
                "edit",
                "patch",
                "bash",
                "grep",
                "glob",
                "find",
                "ls",
                "websearch",
            )
        )
        names = {tool.name for tool in BuildPolicy().filter_tools(tools)}
        assert {"read", "write", "edit", "patch", "bash"} <= names
        assert "websearch" in names
        assert not names & {
            "grep",
            "glob",
            "find",
            "ls",
        }

    def test_act_hides_structured_search_helpers(self) -> None:
        tools = tuple(
            _tool(name)
            for name in (
                "read",
                "write",
                "edit",
                "patch",
                "bash",
                "grep",
                "glob",
            )
        )
        names = {tool.name for tool in ActPolicy().filter_tools(tools)}
        assert names == {"read", "write", "edit", "patch", "bash"}

    def test_plan_uses_bash_instead_of_structured_search_helpers(self) -> None:
        tools = (
            _tool("read"),
            _tool("bash"),
            _tool("grep"),
            _tool("glob"),
        )
        names = {tool.name for tool in PlanPolicy().filter_tools(tools)}
        assert names == {"read", "bash"}


class TestPlanPolicy:
    def test_filter_keeps_read_tools(self) -> None:
        from cade.agent.types import ToolSpec

        tools = (
            ToolSpec(
                name="read", description="", input_hint="", handler=lambda d, _: ""
            ),
            ToolSpec(
                name="bash", description="", input_hint="", handler=lambda d, _: ""
            ),
        )
        filtered = PlanPolicy().filter_tools(tools)
        names = {t.name for t in filtered}
        assert "read" in names
        assert "bash" in names


class TestExecutionModeState:
    def test_plan_timeout_switches_to_build(self) -> None:
        state = ExecutionModeState(max_plan_turns=3)
        state.set_mode("plan")
        for _ in range(2):
            assert not state.check_plan_timeout()
        assert state.check_plan_timeout()
        assert state.current_mode == "build"

    def test_non_plan_timeout_noop(self) -> None:
        state = ExecutionModeState()
        assert not state.check_plan_timeout()
        assert state.current_mode == "act"

    def test_router_auto_forces_auto_review(self) -> None:
        state = ExecutionModeState(approval_router="auto")
        assert state.approvals_reviewer == "auto_review"
        state.set_mode("plan")
        assert state.approvals_reviewer == "auto_review"

    def test_router_user_forces_user_review(self) -> None:
        state = ExecutionModeState(initial_mode="build", approval_router="user")
        assert state.approvals_reviewer == "user"

    def test_tool_gate_freezes_shell_policy_for_current_mode(self) -> None:
        state = ExecutionModeState()

        def user(_request: ApprovalRequest) -> HITLResult:
            return HITLResult("allow", "once")

        def auto(_request: ApprovalRequest) -> HITLResult:
            return HITLResult("allow", "once")

        gate = ToolGate(
            mode_state=state,
            user_approval_callback=user,
            auto_approval_callback=auto,
            permission_policy=None,
            hook_manager=None,
            audit_logger=None,
            session_id="test",
            shell_unresolved_policies=DEFAULT_SHELL_UNRESOLVED_POLICIES,
        )

        state.set_mode("plan")
        plan_snapshot = gate.snapshot()
        state.set_mode("build")
        build_snapshot = gate.snapshot()
        state.set_mode("act")
        act_snapshot = gate.snapshot()

        assert plan_snapshot.shell_unresolved_policy == "ask"
        assert plan_snapshot.approvals_reviewer == "auto_review"
        assert plan_snapshot.approval_callback is auto
        assert build_snapshot.shell_unresolved_policy == "ask"
        assert build_snapshot.approvals_reviewer == "auto_review"
        assert build_snapshot.approval_callback is auto
        assert act_snapshot.shell_unresolved_policy == "ask"
        assert act_snapshot.approvals_reviewer == "user"
        assert act_snapshot.approval_callback is user
