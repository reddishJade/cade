"""权限证据必须随工具调用闭包传递，不能按共享 tool_call_id 查找。"""

from __future__ import annotations

import pytest

from cade.agent.config import AfterToolCallContext, AgentContext, BeforeToolCallContext
from cade.agent.messages import AssistantMessage
from cade.agent.types import AgentToolResult, TextContent, ToolCallContent
from cade.coding_agent.execution_modes import ExecutionModeState
from cade.harness.agent_runtime.tool_gate import ToolGate
from cade.harness.security.permission_model import ApprovalResult
from cade.harness.security.permissions import PermissionEngineResult


def test_same_call_id_interleaved_approvals_keep_independent_audits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = []
    gate = ToolGate(
        mode_state=ExecutionModeState(initial_mode="act"),
        user_approval_callback=None,
        auto_approval_callback=None,
        permission_policy=None,
        hook_manager=None,
        audit_logger=records.append,
        session_id="test-session",
    )

    def decide(
        _name: str,
        args: dict[str, object],
        _decision: object,
        _snapshot: object,
        **_kwargs: object,
    ) -> PermissionEngineResult:
        return PermissionEngineResult(
            decision="allow",
            blocked=False,
            source=str(args["tag"]),
            approval_result=ApprovalResult(decision="allow", scope="once"),
            metadata={
                "approval_reviewer": "auto_review",
                "approval_decision": "allow",
                "approval_risk": "low",
                "approval_authorization": "explicit",
                "approval_rationale": f"approved {args['tag']}",
            },
        )

    monkeypatch.setattr(gate, "_precheck_permission", decide)
    before = gate.build_before_tool_hook(gate.snapshot())
    after = gate.build_after_tool_hook()

    def attempt(tag: str) -> tuple[AfterToolCallContext, object]:
        tool = ToolCallContent(id="reused-id", name="bash", arguments={"tag": tag})
        context = AgentContext()
        result = before(
            BeforeToolCallContext(
                assistant_message=AssistantMessage(content=[tool]),
                tool_call=tool,
                args={"tag": tag},
                context=context,
            ),
            None,
        )
        assert result is not None
        assert not result.block
        outcome = AgentToolResult(content=[TextContent(text="ok")])
        after_context = AfterToolCallContext(
            assistant_message=AssistantMessage(content=[tool]),
            tool_call=tool,
            args={"tag": tag},
            result=outcome,
            is_error=False,
            context=context,
            permission_result=result.permission_result,
        )
        return after_context, result.permission_result

    first, first_permission = attempt("first")
    second, second_permission = attempt("second")
    assert first_permission is not second_permission

    # 两个调用共享同一个 tool_call ID，但先完成第二个。
    after(second, None)
    after(first, None)
    assert [record.approval_rationale for record in records] == [
        "approved second",
        "approved first",
    ]
    assert "approved second" in str(second.result.details)
    assert "approved first" in str(first.result.details)
    assert not hasattr(gate, "_last_perm_results")
