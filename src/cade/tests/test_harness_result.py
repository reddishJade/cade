"""Agent 运行结果纯函数单元测试。"""

from __future__ import annotations

from cade.harness.agent_runtime.result import RunState


class TestRunState:
    def test_from_dict_non_dict(self) -> None:
        state = RunState.from_dict("not a dict")
        assert state.messages == []
