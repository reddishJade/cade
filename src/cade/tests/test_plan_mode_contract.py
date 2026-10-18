"""Plan mode remains active until a user explicitly selects another mode."""

from cade.coding_agent.execution_modes import ExecutionModeState, mode_notice


def test_plan_mode_does_not_have_automatic_timeout() -> None:
    state = ExecutionModeState(initial_mode="plan")
    assert not hasattr(state, "check_plan_timeout")
    for _ in range(32):
        assert state.current_mode == "plan"
        assert "Do not modify project code" in mode_notice(state.current_mode)


def test_only_explicit_selection_changes_plan_mode() -> None:
    state = ExecutionModeState(initial_mode="plan")
    assert state.current_mode == "plan"
    state.set_mode("act")
    assert state.current_mode == "act"
    state.set_mode("plan")
    assert state.current_mode == "plan"
    state.set_mode("build")
    assert state.current_mode == "build"
