"""保护选定块完整纳入、预算拒绝和换窗后目录刷新契约。

控制性回归使用真实组装器、策略和循环；拒绝场景的 provider 只检测误发送，
不模拟服务协议。文件场景在仓库 e2e-results 下创建独立目录，不修改真实 Memory。
运行：uv run --locked pytest -q src/cade/tests/test_context_contract.py。
真实宿主及服务验收另通过 cade exec 执行。
计量使用 conftest 中的本地单字节词表，不依赖下载或缓存。
特殊标记回归使用 100 个「a 」及标记和 90 token 预算：
正文至少 212 字节，真实编码必须拒绝发送；误入字节兜底则只计约 71 token。
"""

from __future__ import annotations

import asyncio
import json
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
    ContextCollectorRegistry,
    ContextManager,
    ContextPolicy,
    ContextPriority,
    DefaultRequestAssembler,
    FrozenContextCollectorRegistry,
    TerminationReason,
    make_collector_section,
    make_state_section,
    run_agent_loop,
)
from cade.agent.context import ContextBlockTarget
from cade.agent.messages import (
    AgentMessage,
    AssistantMessage,
    ContextMessage,
    SystemMessage,
    ToolResultMessage,
    UserMessage,
)
from cade.agent.types import TextContent, ToolCallContent
from cade.ai.events import FinalMessage, Message, ProviderEvent
from cade.ai.types import StreamOptions, ToolDefinition
from cade.harness.agent_runtime.context_window import ContextWindowRollover
from cade.harness.agent_runtime.result import build_structured_result
from cade.harness.memory.catalog import MemoryCatalogCollector


class SelectedContext:
    def __init__(self, blocks: list[ContextBlock]) -> None:
        self.blocks = blocks

    def collect(self, input: object) -> list[ContextBlock]:
        return self.blocks

    def freeze(self) -> FrozenContextCollectorRegistry:
        registry = ContextCollectorRegistry()
        registry.register_section(make_collector_section("selected", self))
        return registry.freeze()


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


def policy(budget: int = 8192, *, next_input: int = 0) -> ContextPolicy:
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


def wire_message_bytes(
    messages: list[Message] | tuple[Message, ...],
) -> tuple[bytes, ...]:
    return tuple(
        json.dumps(
            message,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        for message in messages
    )


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
        request_token_budget=8192,
    )
    assembly = DefaultRequestAssembler(
        context_collectors=SelectedContext(blocks).freeze()
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


def test_dynamic_user_context_stays_after_retained_history() -> None:
    """覆盖缺口：旧回归没有稳定 SKILL 目录，未检查它与动态快照共存。

    运行命令：
    uv run --locked pytest -q \
        src/cade/tests/test_context_contract.py::test_dynamic_user_context_stays_after_retained_history

    预期：目录和历史前缀逐字节不变。所选事实完整进入一条待提交快照，
    位于完整工具结果之后。组装不修改原始历史或快照来源。
    """
    system_context = ContextBlock(
        source=ContextBlockSource.ENVIRONMENT,
        priority=ContextPriority.CONTEXT,
        content="Runtime system context",
        target=ContextBlockTarget.SYSTEM,
        provenance="runtime:environment",
    )
    system_instruction = ContextBlock(
        source=ContextBlockSource.INSTRUCTION,
        priority=ContextPriority.INSTRUCTION,
        content="Project system instruction",
        target=ContextBlockTarget.SYSTEM,
        provenance="project:instruction",
    )
    notes = ContextBlock(
        source=ContextBlockSource.NOTES,
        priority=ContextPriority.CONTEXT,
        content="Old request note snapshot",
        provenance="session:notes",
        scope="runtime",
    )
    skill_catalog = ContextBlock(
        source=ContextBlockSource.SKILL,
        priority=ContextPriority.CONTEXT,
        content="Stable skill catalog entry. " * 120,
        provenance="runtime:skills",
        scope="runtime",
    )
    selected = SelectedContext([system_context, skill_catalog, system_instruction])
    assembler = DefaultRequestAssembler(context_collectors=selected.freeze())
    context = AgentContext(
        messages=[
            SystemMessage(content="Base system instruction"),
            UserMessage(content="Retained task"),
            AssistantMessage(
                content=[
                    ToolCallContent(
                        id="retained-call",
                        name="read",
                        arguments={"path": "example.txt"},
                    )
                ]
            ),
            ToolResultMessage(
                tool_call_id="retained-call",
                tool_name="read",
                content="Retained tool result",
            ),
            AssistantMessage(content=[TextContent(text="Retained answer")]),
        ],
        context_policy=policy(),
    )
    original_messages = tuple(message.model_dump_json() for message in context.messages)

    first = assembler.assemble(context, current_step=1, options=None)
    first.require_sendable()
    assert not any("snapshot" in str(message) for message in first.wire_messages)

    stale_notes = notes
    stale_validation = ContextBlock(
        source=ContextBlockSource.RECENT_VALIDATION,
        priority=ContextPriority.CONTEXT,
        content="Old validation snapshot",
        provenance="session:validation",
        scope="runtime",
    )
    selected.blocks = [
        system_context,
        skill_catalog,
        stale_notes,
        stale_validation,
        system_instruction,
    ]
    second = assembler.assemble(context, current_step=2, options=None)
    second.require_sendable()

    latest_notes = ContextBlock(
        source=ContextBlockSource.NOTES,
        priority=ContextPriority.CONTEXT,
        content="Current request note snapshot",
        provenance="session:notes",
        scope="runtime",
    )
    latest_validation = ContextBlock(
        source=ContextBlockSource.RECENT_VALIDATION,
        priority=ContextPriority.CONTEXT,
        content="Current validation snapshot",
        provenance="session:validation",
        scope="runtime",
    )
    selected.blocks = [
        system_context,
        skill_catalog,
        latest_notes,
        latest_validation,
        system_instruction,
    ]
    third = assembler.assemble(context, current_step=3, options=None)
    third.require_sendable()

    first_wire = first.wire_messages
    second_wire = second.wire_messages
    third_wire = third.wire_messages
    assert [message["role"] for message in first_wire[:3]] == [
        "system",
        "system",
        "system",
    ]
    assert [message["content"] for message in first_wire[:3]] == [
        "Base system instruction",
        "Project system instruction",
        "Runtime system context",
    ]
    skill_text = f"[skill]\n{skill_catalog.content}"
    assert first_wire[3]["content"] == skill_text
    assert first_wire[4]["content"] == "Retained task"
    retained_prefix_length = len(first_wire)
    retained_prefix = wire_message_bytes(first_wire)
    assert wire_message_bytes(second_wire[:retained_prefix_length]) == retained_prefix
    assert wire_message_bytes(third_wire[:retained_prefix_length]) == retained_prefix

    assert first_wire[5]["role"] == "assistant"
    tool_calls = first_wire[5]["tool_calls"]
    assert isinstance(tool_calls, list)
    assert tool_calls[0]["id"] == "retained-call"
    assert first_wire[6] == {
        "role": "tool",
        "tool_call_id": "retained-call",
        "content": "Retained tool result",
    }
    assert first_wire[7]["content"] == "Retained answer"

    stale_notes_text = "[notes]\nOld request note snapshot"
    stale_validation_text = "[recent_validation]\nOld validation snapshot"
    latest_notes_text = "[notes]\nCurrent request note snapshot"
    latest_validation_text = "[recent_validation]\nCurrent validation snapshot"
    assert second.context_message is not None
    assert third.context_message is not None
    assert stale_notes_text in second_wire[-1]["content"]
    assert stale_validation_text in second_wire[-1]["content"]
    assert latest_notes_text in third_wire[-1]["content"]
    assert latest_validation_text in third_wire[-1]["content"]
    assert sum(message.get("content") == skill_text for message in third_wire) == 1
    assert third.context_message.content.count(latest_notes_text) == 1
    assert third.context_message.content.count(latest_validation_text) == 1
    assert all(
        stale not in str(message)
        for message in third_wire
        for stale in (stale_notes_text, stale_validation_text)
    )
    assert second_wire[-1]["role"] == third_wire[-1]["role"] == "user"
    note_trace = next(trace for trace in third.context_trace if trace.source == "notes")
    validation_trace = next(
        trace for trace in third.context_trace if trace.source == "recent_validation"
    )
    assert note_trace.provenance == "session:notes"
    assert validation_trace.provenance == "session:validation"
    assert tuple(message.model_dump_json() for message in context.messages) == (
        original_messages
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("retry_first", [False, True])
async def test_runtime_context_is_append_only_deduplicated_and_explicitly_cleared(
    retry_first: bool,
) -> None:
    """覆盖缺口：临时快照会从下一次真实循环请求中消失。

    运行本模块；受控 provider 可先失败一次再完成。独立比较请求字节前缀和
    事实消息数，验证重试、变化、去重及删除，不推断服务缓存命中率。
    """

    class Provider:
        def __init__(self) -> None:
            self.requests: list[list[Message]] = []

        async def stream(
            self,
            messages: list[Message],
            tools: list[ToolDefinition],
            options: StreamOptions | None = None,
            **kwargs: object,
        ) -> AsyncIterator[ProviderEvent]:
            self.requests.append(messages)
            if retry_first and len(self.requests) == 1:
                yield FinalMessage("Temporary provider error", "error")
                return
            yield FinalMessage("ACK", "end_turn")

    old = ContextBlock(
        source=ContextBlockSource.RECENT_VALIDATION,
        priority=ContextPriority.CONTEXT,
        content="Previous validation passed.",
    )
    current = ContextBlock(
        source=ContextBlockSource.RECENT_VALIDATION,
        priority=ContextPriority.CONTEXT,
        content="Current validation failed.",
    )
    selected = SelectedContext([old])
    provider = Provider()
    manager = ContextManager()
    config = AgentLoopConfig(
        provider=provider,
        request_assembler=DefaultRequestAssembler(context_collectors=selected.freeze()),
        rollover_context=ContextWindowRollover().rollover_messages,
        retry_backoff_base=0,
    )
    for step, blocks in enumerate(([old], [old], [current], [], []), start=1):
        selected.blocks = blocks
        result = await collect_loop_result(
            [UserMessage(content=f"Task {step}")],
            AgentContext(
                messages=manager.history_messages(),
                context_manager=manager,
                context_policy=policy(64_000),
            ),
            config,
        )
        assert result.termination_reason == TerminationReason.COMPLETED
        contexts = [m for m in result.surface if isinstance(m, ContextMessage)]
        assert len(contexts) == (1, 1, 2, 3, 3)[step - 1]
        if step > 1:
            previous = provider.requests[-2]
            assert wire_message_bytes(provider.requests[-1][: len(previous)]) == (
                wire_message_bytes(previous)
            )
    if retry_first:
        assert wire_message_bytes(provider.requests[0]) == wire_message_bytes(
            provider.requests[1]
        )
    assert old.content in contexts[0].content
    assert current.content in contexts[1].content
    assert "supersedes earlier" in contexts[1].content
    assert "no longer apply" in contexts[2].content
    assert "Previous validation passed." in str(provider.requests[-1])
    assert "Current validation failed." in str(provider.requests[-1])


def test_tool_evidence_prefix_is_stable_when_new_results_arrive() -> None:
    """覆盖滚动配额改写旧正文的缺口；新结果不能改变已准入的请求前缀。"""
    context = AgentContext(
        messages=[UserMessage(content="Task"), *tool_group("old", "OLD " * 500)],
        context_policy=ContextPolicy(
            physical_window=10_000,
            output_reserve=0,
            headroom_tokens=0,
            evidence_token_budget=600,
            next_turn_input_tokens=0,
        ),
    )
    assembler = DefaultRequestAssembler()
    first = assembler.assemble(context, current_step=1, options=None)
    first.require_sendable()
    context.messages.extend(tool_group("new", "NEW " * 500))
    original = [message.model_dump() for message in context.messages]
    second = assembler.assemble(context, current_step=2, options=None)
    second.require_sendable()

    assert wire_message_bytes(second.wire_messages[: len(first.wire_messages)]) == (
        wire_message_bytes(first.wire_messages)
    )
    results = [m for m in second.messages if isinstance(m, ToolResultMessage)]
    assert all("Preview is incomplete" in str(message.content) for message in results)
    assert "OLD" in str(results[0].content) and "NEW" in str(results[1].content)
    assert [message.model_dump() for message in context.messages] == original
    assert second.estimated_tokens < second.context_policy.admission_target


def test_explicit_evidence_reclamation_still_prioritizes_recent_results() -> None:
    """窗口压力仍可回收旧正文；稳定准入不能绕过显式回收预算。"""
    history = [*tool_group("old", "OLD " * 500), *tool_group("new", "NEW " * 500)]
    projected, omitted = policy().project_evidence(history, token_budget=600)
    results = [
        message for message in projected if isinstance(message, ToolResultMessage)
    ]
    assert omitted == ("old", "new")
    assert "OLD" not in str(results[0].content)
    assert "NEW" in str(results[1].content)
    assert "OLD" in str(history[1].content)


@pytest.mark.asyncio
@pytest.mark.parametrize("automatic_rollover", [False, True])
async def test_window_pressure_rotates_before_rewriting_cached_evidence(
    automatic_rollover: bool,
) -> None:
    """回收能掩盖总窗口压力；自动换窗必须按回收前预测触发一次。"""

    class Provider:
        def __init__(self) -> None:
            self.requests: list[list[Message]] = []

        async def stream(
            self,
            messages: list[Message],
            tools: list[ToolDefinition],
            options: StreamOptions | None = None,
            **kwargs: object,
        ) -> AsyncIterator[ProviderEvent]:
            self.requests.append(messages)
            yield FinalMessage("ACK", "end_turn")

    provider = Provider()
    context = AgentContext(
        messages=[
            UserMessage(content="Completed task " * 2000),
            AssistantMessage(content=[TextContent(text="Completed answer " * 2000)]),
            UserMessage(content="Current task"),
            *tool_group("old", "Old evidence " * 1000),
            *tool_group("current", "Current evidence " * 1000),
        ]
    )
    context_policy = ContextPolicy(
        physical_window=8192,
        output_reserve=0,
        headroom_tokens=0,
        next_turn_input_tokens=0,
        automatic_rollover=automatic_rollover,
    )
    result = await collect_loop_result(
        [],
        context,
        AgentLoopConfig(
            provider=provider,
            context_policy=context_policy,
            rollover_context=ContextWindowRollover().rollover_messages,
        ),
    )
    assert result.termination_reason == TerminationReason.COMPLETED
    assert result.metrics is not None
    assert result.metrics.context_window_resets == int(automatic_rollover)
    assert len(provider.requests) == 1
    assert ("context-window-reset" in str(provider.requests[0])) == automatic_rollover


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
    assert block.get_token_count() == 200 + len(marker)
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
        context_collectors=SelectedContext(blocks).freeze()
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
        context_collectors=SelectedContext(blocks).freeze()
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
    registry = ContextCollectorRegistry()
    registry.register_section(
        make_state_section("environment", "environment", ContextBlockSource.ENVIRONMENT)
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
                request_assembler=DefaultRequestAssembler(
                    context_collectors=registry.freeze()
                ),
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
    registry = ContextCollectorRegistry()
    registry.register_section(
        make_collector_section("memory", MemoryCatalogCollector())
    )
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
    assembler = DefaultRequestAssembler(context_collectors=registry.freeze())
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
