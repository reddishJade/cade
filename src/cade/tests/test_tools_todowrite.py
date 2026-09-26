"""todowrite 工具验证逻辑单元测试。"""

from __future__ import annotations

import json

from cade.coding_agent.tools.todowrite import build_todowrite_tool
from cade.harness.session_todo import SessionTodoState

tool = build_todowrite_tool()


class TestTodoWriteValidation:
    def test_missing_todos(self) -> None:
        result = tool.handler({}, None)
        assert "must be an array" in result

    def test_empty_content_raises(self) -> None:
        result = tool.handler({"todos": [{"content": "", "status": "pending"}]}, None)
        assert "must not be empty" in result

    def test_invalid_status_raises(self) -> None:
        result = tool.handler(
            {"todos": [{"content": "task", "status": "invalid"}]}, None
        )
        assert "invalid todo status" in result

    def test_invalid_priority_raises(self) -> None:
        result = tool.handler(
            {"todos": [{"content": "task", "status": "pending", "priority": "urgent"}]},
            None,
        )
        assert "invalid todo priority" in result

    def test_updates_shared_session_state(self) -> None:
        state = SessionTodoState()
        stateful_tool = build_todowrite_tool(state)
        result = stateful_tool.handler(
            {
                "todos": [
                    {
                        "id": "fix-bug",
                        "content": "Fix bug",
                        "status": "in_progress",
                        "priority": "high",
                    }
                ]
            },
            None,
        )
        payload = json.loads(result)
        assert payload["todos"][0]["id"] == "fix-bug"
        assert (
            'id="fix-bug" status="in_progress" priority="high": Fix bug'
            in state.render_context()
        )

    def test_rejects_multiple_in_progress_items(self) -> None:
        result = tool.handler(
            {
                "todos": [
                    {"id": "one", "content": "One", "status": "in_progress"},
                    {"id": "two", "content": "Two", "status": "in_progress"},
                ]
            },
            None,
        )
        assert "at most one" in result
