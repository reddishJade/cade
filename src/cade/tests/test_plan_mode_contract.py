"""执行模式的显式控制契约。

经过真实 CodingAgentHarness / AgentLoopConfig / Agent Loop 的多轮工具执行，
验证 Plan 不随步数改变，只有调用 set_mode 才改变权限模式。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from pathlib import Path

import pytest

from cade.agent.request import DefaultRequestAssembler
from cade.agent.types import ToolSpec
from cade.ai.events import FinalMessage, Message, ProviderEvent, ToolCall, ToolCallEvent
from cade.ai.providers.base import Provider
from cade.ai.types import StreamOptions, ToolDefinition
from cade.ai.usage import UsageTotals
from cade.coding_agent.execution_modes import (
    ExecutionModeState,
    build_default_mode_rulesets,
)
from cade.coding_agent.harness import CodingAgentHarness
from cade.coding_agent.runtime import CodingAgentRuntimeConfig
from cade.coding_agent.state import CodingRunState
from cade.harness.agent_runtime.composition import AgentComposition
from cade.harness.agent_runtime.config import GateConfig
from cade.harness.config import AgentConfig
from cade.harness.session import SessionInbox, SessionStore


class _TenStepProvider(Provider):
    """每次发送唯一 read 调用，十次后结束；记录模型调用时的实际模式。"""

    def __init__(self) -> None:
        super().__init__()
        self._model = "gpt-4o-mini"
        self._base_url = "https://provider.invalid"
        self._transport = "openai_chat"
        self._thinking = False
        self._context_window = 32_000
        self.calls = 0
        self.modes: list[str] = []
        self.current_mode: Callable[[], str] = lambda: "plan"

    @property
    def usage_totals(self) -> UsageTotals:
        return UsageTotals()

    @property
    def cache_hit_rate(self) -> float | None:
        return None

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolDefinition],
        options: StreamOptions | None = None,
        **kwargs: object,
    ) -> AsyncIterator[ProviderEvent]:
        self.calls += 1
        self.modes.append(self.current_mode())
        if self.calls <= 10:
            yield ToolCallEvent(
                calls=[
                    ToolCall(
                        id=f"read-{self.calls}",
                        name="read",
                        input={"path": f"step-{self.calls}.txt"},
                    )
                ]
            )
            yield FinalMessage(content="", stop_reason="tool_use")
        else:
            yield FinalMessage(
                content="Investigation complete.", stop_reason="end_turn"
            )


def test_only_explicit_selection_changes_mode() -> None:
    """模式状态只随调用者显式选择改变。"""
    state = ExecutionModeState(initial_mode="plan")
    assert state.current_mode == "plan"
    state.set_mode("act")
    assert state.current_mode == "act"
    state.set_mode("plan")
    assert state.current_mode == "plan"
    state.set_mode("build")
    assert state.current_mode == "build"


@pytest.mark.asyncio
async def test_plan_persists_through_ten_tool_steps(tmp_path: Path) -> None:
    """执行超过旧八轮阈值，真实模式仍为 Plan，后续只能显式切换。"""
    provider = _TenStepProvider()
    executed: list[str] = []
    tool = ToolSpec(
        name="read",
        description="Mock read-only operation",
        input_hint="path",
        handler=lambda args, _update: executed.append(str(args["path"])) or "ok",
        schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    )
    composition = AgentComposition.create(
        primary_provider=provider,
        registry=(tool,),
        config=AgentConfig(max_steps=12, automatic_rollover=False),
        gate=GateConfig(default_mode_rulesets=build_default_mode_rulesets()),
        request_assembler=DefaultRequestAssembler(),
        runtime_context_provider=None,
    )
    harness = CodingAgentHarness(
        composition,
        CodingAgentRuntimeConfig(
            session_inbox=SessionInbox(SessionStore(tmp_path / "sessions")),
            initial_mode="plan",
        ),
    )
    provider.current_mode = lambda: harness.current_mode
    result = await harness.run_async("Investigate ten separate steps.")

    assert provider.calls == 11
    assert provider.modes == ["plan"] * 11
    assert len(executed) == 10
    assert isinstance(result.run_state, CodingRunState)
    assert result.run_state.current_mode == "plan"
    assert harness.current_mode == "plan"
    harness.set_mode("build")
    assert harness.current_mode == "build"
