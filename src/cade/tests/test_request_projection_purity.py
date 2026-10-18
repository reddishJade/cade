"""组装 provider 请求只规范化投影，不改写历史。"""

from cade.agent.config import AgentContext
from cade.agent.messages import AssistantMessage, ToolResultMessage, UserMessage
from cade.agent.request import DefaultRequestAssembler
from cade.agent.types import ToolCallContent


def test_request_repair_does_not_mutate_source_history() -> None:
    dangling = ToolCallContent(id="missing", name="read", arguments={"path": "a.py"})
    context = AgentContext(
        messages=[
            UserMessage(content="Inspect a.py"),
            AssistantMessage(content=[dangling]),
            UserMessage(content="Continue after interruption"),
            ToolResultMessage(
                tool_call_id="orphan", tool_name="read", content="orphan"
            ),
        ]
    )
    before = [m.model_dump_json() for m in context.messages]
    assembler = DefaultRequestAssembler()
    first = assembler.assemble(context, current_step=1, options=None)
    second = assembler.assemble(context, current_step=2, options=None)
    first.require_sendable()
    second.require_sendable()
    assert [m.model_dump_json() for m in context.messages] == before
    assert first.wire_messages == second.wire_messages
    assert all("tool_calls" not in m for m in first.wire_messages)
    assert all(m.get("role") != "tool" for m in first.wire_messages)
