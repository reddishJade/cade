"""换窗带有界原始操作索引，避免未验证阶段不断重读。"""

from __future__ import annotations

import json
from pathlib import Path

from cade.agent.messages import (
    AgentMessage,
    AssistantMessage,
    SystemMessage,
    ToolResultMessage,
    UserMessage,
)
from cade.agent.types import ToolCallContent
from cade.harness.agent_runtime.context_window import ContextWindowRollover


def test_rollover_indexes_completed_actions_without_outputs(tmp_path: Path) -> None:
    messages: list[AgentMessage] = [UserMessage(content="repair the original task")]
    for number in range(8):
        messages.extend(
            [
                AssistantMessage(
                    content=[
                        ToolCallContent(
                            id=f"read-{number}",
                            name="read_file",
                            arguments={"path": f"module-{number}.py"},
                        )
                    ]
                ),
                ToolResultMessage(
                    tool_call_id=f"read-{number}",
                    tool_name="read_file",
                    content="huge output" * 10000,
                    is_error=number == 7,
                ),
            ]
        )
    messages.append(
        AssistantMessage(
            content=[
                ToolCallContent(
                    id="pending",
                    name="bash",
                    arguments={"command": "not completed"},
                )
            ]
        )
    )
    snapshot = [m.model_dump() for m in messages]
    window = ContextWindowRollover().rollover_messages(messages)
    notices = [m.content for m in window if isinstance(m, SystemMessage)]
    notice = "\n".join(notices)
    assert "<recent-tool-actions>" in notice
    assert "module-7.py" in notice and "read-7" in notice and "error" in notice
    assert "module-2.py" in notice
    assert "module-1.py" not in notice
    assert '"tool_call_id": "pending"' not in notice
    assert "huge output" not in notice
    assert len(notice.encode()) < 4096
    assert len(window) == 2
    assert [m.model_dump() for m in messages] == snapshot
    second = ContextWindowRollover().rollover_messages(window)
    assert "<recent-tool-actions>" not in "\n".join(
        m.content for m in second if isinstance(m, SystemMessage)
    )
    (tmp_path / "action-handoff.json").write_text(
        json.dumps(
            [m.model_dump(mode="json") for m in window],
            ensure_ascii=False,
            indent=2,
        )
    )
