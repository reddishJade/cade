"""Provider RequestAssembly 单一组装边界测试。"""

from __future__ import annotations

from cade.agent.config import AgentContext
from cade.agent.context import (
    ContextBlock,
    ContextBlockSource,
    ContextBlockTarget,
    ContextCollectionInput,
    ContextCollectorRegistry,
    ContextExpiry,
    ContextPriority,
)
from cade.agent.messages import (
    SystemMessage,
    UserMessage,
)
from cade.agent.request import DefaultRequestAssembler
from cade.agent.types import ToolSpec, ToolSpecAdapter


class _Collector:
    def collect(self, _input: ContextCollectionInput) -> list[ContextBlock]:
        return [
            ContextBlock(
                source=ContextBlockSource.NOTES,
                priority=ContextPriority.HIGH,
                target=ContextBlockTarget.SYSTEM,
                content="current architecture note",
                block_id="note-current",
                provenance="AGENTS.md",
                truncated=True,
                truncation_reason="byte_budget",
            ),
            ContextBlock(
                source=ContextBlockSource.MODE,
                priority=ContextPriority.LOW,
                content="expired diff",
                block_id="diff-old",
                expiry=ContextExpiry(max_steps=1),
                created_step=0,
            ),
        ]


def test_request_assembly_is_the_complete_provider_envelope() -> None:
    collectors = ContextCollectorRegistry()
    collectors.register(_Collector())
    tool = ToolSpecAdapter(
        ToolSpec(
            name="read_file",
            description="Read one local file.",
            input_hint="path",
            handler=lambda _data, _update=None: "contents",
            schema={"type": "object", "properties": {}},
        )
    )
    assembler = DefaultRequestAssembler(
        context_collectors=collectors,
    )

    assembly = assembler.assemble(
        AgentContext(
            request_prefix=[SystemMessage(content="identity")],
            messages=[UserMessage(content="inspect")],
            tools=[tool],
        ),
        current_step=2,
        options=None,
    )

    assert [message["role"] for message in assembly.wire_messages] == [
        "system",
        "system",
        "user",
    ]
    assert assembly.wire_messages[1]["content"] == "current architecture note"
    assert [tool.name for tool in assembly.tools] == ["read_file"]
    assert [(trace.block_id, trace.included) for trace in assembly.context_trace] == [
        ("note-current", True),
        ("diff-old", False),
        ("read_file", True),
    ]
    assert all(len(trace.content_digest) == 64 for trace in assembly.context_trace)
    assert assembly.context_trace[0].provenance == "AGENTS.md"
    assert assembly.context_trace[0].truncated
    assert assembly.context_trace[0].truncation_reason == "byte_budget"
    assert assembly.context_trace[2].source == "tool"
