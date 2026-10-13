"""保护选定块完整纳入、预算拒绝和换窗后目录刷新契约。

控制性回归使用真实组装器、策略和循环；拒绝场景的 provider 只检测误发送，
不模拟服务协议。文件场景在仓库 e2e-results 下创建独立目录，不修改真实 Memory。
运行：uv run --locked pytest -q src/cade/tests/test_context_contract.py。
真实宿主及服务验收另通过 cade exec 执行。
特殊标记回归使用无外部服务的选定指令块（100 个「a 」及标记）和 90 token
预算；已核对的 cl100k_base 普通文本计数为 106，必须拒绝发送。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from cade.agent import (
    AgentContext,
    AgentLoopConfig,
    AgentLoopResult,
    ContextAssemblyError,
    ContextBlock,
    ContextBlockSource,
    ContextManager,
    ContextPolicy,
    ContextPriority,
    ContextSection,
    DefaultRequestAssembler,
    TerminationReason,
    make_state_section,
    run_agent_loop,
)
from cade.agent.messages import (
    AgentMessage,
    AssistantMessage,
    SystemMessage,
    ToolResultMessage,
    UserMessage,
)
from cade.agent.types import TextContent, ToolCallContent
from cade.ai.events import Message, ProviderEvent
from cade.ai.types import StreamOptions, ToolDefinition
from cade.harness.agent_runtime.context_window import ContextWindowRollover
from cade.harness.agent_runtime.result import build_structured_result
from cade.harness.memory.catalog import MemoryCatalogCollector


class SelectedContext:
    def __init__(self, blocks: list[ContextBlock]) -> None:
        self.blocks = blocks

    def collect(self, input: object) -> list[ContextBlock]:
        return self.blocks

    def sections(self) -> tuple[ContextSection, ...]:
        return (ContextSection("selected", self.collect),)


class UncalledProvider:
    def stream(
        self,
        messages: list[Message],
        tools: list[ToolDefinition],
        options: StreamOptions | None = None,
        **kwargs: object,
    ) -> AsyncIterator[ProviderEvent]:
        raise AssertionError("An incomplete or oversized request was sent")


async def collect_loop_result(
    prompts: list[AgentMessage], context: AgentContext, config: AgentLoopConfig
) -> AgentLoopResult:
    result = None
    async for event in run_agent_loop(prompts, context, config):
        if isinstance(event, AgentLoopResult):
            result = event
    assert result is not None
    return result


def policy(budget: int = 2048, *, next_input: int = 0) -> ContextPolicy:
    return ContextPolicy(
        physical_window=budget,
        output_reserve=0,
        headroom_tokens=0,
        trigger_ratio=1,
        next_turn_input_tokens=next_input,
    )


def selected_blocks() -> list[ContextBlock]:
    return [
        ContextBlock(
            source=ContextBlockSource.SKILL,
            priority=ContextPriority.CONTEXT,
            content="Skill catalog must survive. " * 100,
        ),
        ContextBlock(
            source=ContextBlockSource.INSTRUCTION,
            priority=ContextPriority.INSTRUCTION,
            content="Project rule must survive.",
        ),
        ContextBlock(
            source=ContextBlockSource.MEMORY,
            priority=ContextPriority.CONTEXT,
            content="Memory catalog must survive. " * 100,
        ),
    ]


def tool_group(call_id: str, text: str, *, durable: bool = False) -> list[AgentMessage]:
    return [
        AssistantMessage(
            content=[
                ToolCallContent(id=call_id, name="read", arguments={"path": call_id})
            ]
        ),
        ToolResultMessage(
            tool_call_id=call_id,
            tool_name="read",
            content=text,
            metadata={"context_lifetime": "durable", "context_key": "state"}
            if durable
            else None,
        ),
    ]


@pytest.mark.parametrize("explicit_policy", [False, True])
def test_selected_blocks_survive_history_and_evidence_pressure(
    explicit_policy: bool,
) -> None:
    blocks = selected_blocks()
    history: list[AgentMessage] = [
        UserMessage(content="Completed task " * 4000),
        AssistantMessage(content=[TextContent(text="Completed answer " * 4000)]),
        UserMessage(content="Continue current task"),
        *tool_group("old", "Old evidence " * 4000),
        *tool_group("current", "Recent evidence " * 4000),
    ]
    before = [m.model_dump() for m in history]
    context = AgentContext(
        messages=history,
        context_policy=policy() if explicit_policy else None,
        request_token_budget=2048,
    )
    assembly = DefaultRequestAssembler(
        context_sections=SelectedContext(blocks).sections()
    ).assemble(context, current_step=1, options=None)
    assembly.require_sendable()
    assert assembly.estimated_tokens <= assembly.token_budget
    wire = str(assembly.wire_messages)
    assert all(block.content in wire for block in blocks)
    assert wire.index(blocks[1].content) < wire.index(blocks[0].content)
    assert wire.index(blocks[0].content) < wire.index(blocks[2].content)
    assert "Continue current task" in wire
    assert "history" in wire
    assert assembly.evidence_omitted
    assert assembly.working_omitted
    assert [m.model_dump() for m in history] == before
    assert assembly.mandatory_estimated_tokens >= sum(
        b.get_token_count() for b in blocks
    )


@pytest.mark.parametrize(
    "marker",
    [
        "<|endoftext|>",
        "<|fim_prefix|>",
        "<|fim_middle|>",
        "<|fim_suffix|>",
        "<|endofprompt|>",
    ],
)
def test_special_token_literals_do_not_bypass_context_budget(marker: str) -> None:
    block = ContextBlock(
        source=ContextBlockSource.INSTRUCTION,
        priority=ContextPriority.INSTRUCTION,
        content="a " * 100 + marker,
    )
    assert block.get_token_count() == 106
    assembly = DefaultRequestAssembler(
        context_collectors=SelectedContext([block]).freeze()
    ).assemble(
        AgentContext(messages=[UserMessage(content="Task")], context_policy=policy(90)),
        current_step=1,
        options=None,
    )
    assert assembly.mandatory_estimated_tokens > assembly.token_budget
    assert not assembly.wire_messages
    with pytest.raises(ContextAssemblyError, match="safe budget"):
        assembly.require_sendable()


def test_safe_budget_is_not_next_turn_soft_target() -> None:
    blocks = selected_blocks()
    assembler = DefaultRequestAssembler(
        context_sections=SelectedContext(blocks).sections()
    )
    context = AgentContext(messages=[UserMessage(content="Task")])
    baseline = assembler.assemble(context, current_step=1, options=None)
    context.context_policy = policy(baseline.estimated_tokens, next_input=100)
    assembly = assembler.assemble(context, current_step=1, options=None)
    assembly.require_sendable()
    assert assembly.context_policy is not None
    assert assembly.estimated_tokens > assembly.context_policy.admission_target
    assert assembly.estimated_tokens == assembly.token_budget


@pytest.mark.parametrize("rollover_enabled", [False, True])
@pytest.mark.parametrize("oversized_durable", [False, True])
def test_unassemblable_context_terminates_without_provider_call(
    rollover_enabled: bool, oversized_durable: bool
) -> None:
    blocks = selected_blocks()
    messages: list[AgentMessage] = [UserMessage(content="Task")]
    if oversized_durable:
        messages.extend(tool_group("durable", "Required state " * 4000, durable=True))
    else:
        blocks[0].content = "Required selected context " * 4000
    assembler = DefaultRequestAssembler(
        context_sections=SelectedContext(blocks).sections()
    )
    context = AgentContext(messages=messages, context_policy=policy())
    projection = assembler.assemble(context, current_step=1, options=None)
    assert projection.assembly_error is not None
    assert not projection.wire_messages
    assert projection.rotation_blocked_reason == "mandatory_input_exceeds_budget"
    with pytest.raises(ContextAssemblyError, match="safe budget"):
        projection.require_sendable()
    result = asyncio.run(
        collect_loop_result(
            [],
            context,
            AgentLoopConfig(
                provider=UncalledProvider(),
                request_assembler=assembler,
                rollover_context=ContextWindowRollover().rollover_messages
                if rollover_enabled
                else None,
            ),
        )
    )
    assert result.termination_reason is TerminationReason.CONTEXT_ERROR
    assert result.metrics is not None and result.metrics.llm_calls == 0
    assert result.metrics.context_window_resets == 0
    assert "safe budget" in build_structured_result(result).answer


def test_section_failure_does_not_send_partial_context() -> None:
    sections = (
        make_state_section(
            "environment", "environment", ContextBlockSource.ENVIRONMENT
        ),
    )
    result = asyncio.run(
        collect_loop_result(
            [],
            AgentContext(
                messages=[UserMessage(content="Task")],
                state={"environment": {"incomparable": {1: "one", "two": 2}}},
                context_policy=policy(),
            ),
            AgentLoopConfig(
                provider=UncalledProvider(),
                request_assembler=DefaultRequestAssembler(context_sections=sections),
                rollover_context=ContextWindowRollover().rollover_messages,
            ),
        )
    )
    assert result.termination_reason is TerminationReason.CONTEXT_ERROR
    assert result.error_detail is not None and "environment" in result.error_detail


def test_rollover_refreshes_catalog_and_preserves_latest_durable_state(
    tmp_path: Path,
) -> None:
    workspace = tmp_path
    memory = workspace / ".cade" / "memory"
    memory.mkdir(parents=True)
    entry = memory / "lesson.md"
    entry.write_text("# Before rollover\n\nOld catalog hint.\n", encoding="utf-8")
    sections = (ContextSection("memory", MemoryCatalogCollector().collect),)
    manager = ContextManager()
    context = AgentContext(
        messages=[
            SystemMessage(content="Startup rule"),
            UserMessage(content="Previous task"),
            *tool_group("old-state", "Superseded state", durable=True),
            UserMessage(content="Pending task"),
            *tool_group("current-state", "Current durable state", durable=True),
            *tool_group("recent", "Recent evidence"),
        ],
        project_root=workspace,
        context_manager=manager,
        context_policy=policy(4096),
    )
    assembler = DefaultRequestAssembler(context_sections=sections)
    first = assembler.assemble(context, current_step=1, options=None)
    assert "Before rollover" in str(first.wire_messages)
    rolled = ContextWindowRollover().rollover_messages(
        context.messages, policy=policy()
    )
    context.messages = manager.complete_rollover(rolled)
    entry.write_text("# After rollover\n\nNew catalog hint.\n", encoding="utf-8")
    second = assembler.assemble(context, current_step=2, options=None)
    second.require_sendable()
    wire = str(second.wire_messages)
    assert "Pending task" in wire and "Startup rule" in wire
    assert "Current durable state" in wire
    assert "Superseded state" not in wire
    assert "After rollover" in wire and "Before rollover" not in wire
    entry.unlink()
    third = assembler.assemble(context, current_step=3, options=None)
    assert not any(trace.source == "memory" for trace in third.context_trace)
