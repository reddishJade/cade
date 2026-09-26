"""换窗后运行时前缀的请求组装回归，不模拟 provider I/O。"""

from __future__ import annotations

import json
from pathlib import Path

from cade.agent.agent_loop import _refresh_request_prefix
from cade.agent.config import AgentContext, AgentLoopConfig
from cade.agent.messages import AgentMessage, SystemMessage, UserMessage
from cade.agent.request import DefaultRequestAssembler


def test_rollover_refreshes_prefix_without_changing_history(tmp_path: Path) -> None:
    old = SystemMessage(
        content="<session-notices>resumed old snapshot</session-notices>"
    )
    context = AgentContext(
        request_prefix=[old, SystemMessage(content="todo: investigate")],
        messages=[UserMessage(content="repair with the original constraints")],
    )
    assembler = DefaultRequestAssembler()
    before = assembler.assemble(context, current_step=1, options=None)
    refreshed = [SystemMessage(content="startup and current mode; todo: done")]

    def provide() -> list[AgentMessage]:
        return refreshed

    config = AgentLoopConfig(refresh_request_prefix=provide)
    context.context_state.reset()
    _refresh_request_prefix(context, config)
    after = assembler.assemble(context, current_step=2, options=None)
    (tmp_path / "requests.json").write_text(
        json.dumps({"before": before.wire_messages, "after": after.wire_messages})
    )
    rendered = json.dumps(after.wire_messages)
    assert "session-notices" not in rendered
    assert "todo: investigate" not in rendered
    assert "startup and current mode; todo: done" in rendered
    assert "repair with the original constraints" in rendered
    assert old.content == "<session-notices>resumed old snapshot</session-notices>"
    assert context.request_prefix is not refreshed


def test_rollover_keeps_prefix_without_refresh_provider() -> None:
    prefix = [SystemMessage(content="permanent startup instructions")]
    context = AgentContext(request_prefix=prefix)
    original = context.request_prefix
    _refresh_request_prefix(context, AgentLoopConfig())
    assert context.request_prefix is original
    assert context.request_prefix == prefix
