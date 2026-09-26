"""提醒在本次真实请求组装中交付，不模拟模型或外部 I/O。"""

from __future__ import annotations

import json
from pathlib import Path

from cade.agent.agent_loop import _prepare_request_context
from cade.agent.config import AgentContext, AgentLoopConfig
from cade.agent.messages import AgentMessage, SystemMessage, UserMessage
from cade.agent.request import DefaultRequestAssembler


def test_budget_reminder_is_claimed_before_the_same_request(tmp_path: Path) -> None:
    context = AgentContext(messages=[UserMessage(content="continue the original task")])
    queue: list[AgentMessage] = []
    notified = False

    def notify(tokens: int) -> bool:
        nonlocal notified
        if notified:
            return False
        assert tokens > 0
        notified = True
        queue.append(
            SystemMessage(
                content="<context-budget-reminder>checkpoint now</context-budget-reminder>"
            )
        )
        return True

    def claim() -> list[AgentMessage]:
        messages = list(queue)
        queue.clear()
        return messages

    config = AgentLoopConfig(prepare_request_context=notify)
    assembler = DefaultRequestAssembler()
    initial = assembler.assemble(context, current_step=1, options=None)
    new_messages: list[AgentMessage] = []
    prepared = _prepare_request_context(
        context, config, new_messages, claim, initial, 1
    )
    rendered = json.dumps(prepared.wire_messages)
    assert "checkpoint now" in rendered
    assert "continue the original task" in rendered
    assert len(new_messages) == 1
    assert not queue
    second = _prepare_request_context(context, config, new_messages, claim, prepared, 2)
    assert second is prepared
    assert len(new_messages) == 1
    (tmp_path / "reminder-request.json").write_text(rendered)
