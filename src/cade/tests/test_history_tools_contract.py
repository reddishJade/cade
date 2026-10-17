from __future__ import annotations

import os
from pathlib import Path

import pytest

from cade.harness.session import SessionHistory, SessionStore
from cade.harness.session.history import build_history_tools


class TestHistoryToolsContract:
    """验证历史记录工具的窗口召回、关键词搜索过滤、分页读取及安全边界。"""

    def test_invalid_operation_returns_actionable_error(self, tmp_path: Path) -> None:
        sessions_dir = tmp_path / ".cade" / "sessions"
        history = SessionHistory(sessions_dir, project_root=tmp_path)
        tool = build_history_tools(history)[0]

        result = tool.handler({"operation": "unknown_op"})
        assert "operation must be one of: list_windows, search, read, around" in str(
            result
        )

    def test_list_windows_returns_window_records(self, tmp_path: Path) -> None:
        """验证 list_windows 能够精确召回 context_window_reset 事件并格式化呈现。"""
        sessions_dir = tmp_path / ".cade" / "sessions"
        store = SessionStore(sessions_dir, project_root=tmp_path)
        store.append("user", "Hello first window")
        store.append(
            "event",
            {
                "type": "context_window_reset",
                "data": {"window_id": "win-101", "trigger": "budget_exhaustion"},
            },
        )
        store.append("assistant", "Hello second window")

        history = SessionHistory(sessions_dir, project_root=tmp_path)
        history.set_session_id(store.session_id)
        tool = build_history_tools(history)[0]

        result = str(tool.handler({"operation": "list_windows"}))
        assert "window_id=win-101" in result
        assert "trigger=budget_exhaustion" in result

    def test_search_filters_retrieval_loops_and_matches_keywords(
        self, tmp_path: Path
    ) -> None:
        """验证 search 默认过滤历史自身调用（防回显死循环），且关键词匹配优先召回。"""
        sessions_dir = tmp_path / ".cade" / "sessions"
        store = SessionStore(sessions_dir, project_root=tmp_path)

        # 1. 正常的工具调用和结果
        store.append(
            "event",
            {
                "type": "tool_use",
                "data": {
                    "id": "call-1",
                    "name": "bash",
                    "input": {"command": "pytest"},
                },
            },
        )
        store.append(
            "event",
            {
                "type": "tool_result",
                "data": {
                    "tool_use_id": "call-1",
                    "content": "All tests passed successfully with 100% coverage",
                },
            },
        )

        # 2. 检索自身 tool 调用（应被过滤，除非 include_derived=True）
        store.append(
            "event",
            {
                "type": "tool_use",
                "data": {
                    "id": "hist-call",
                    "name": "history",
                    "input": {"query": "coverage"},
                },
            },
        )
        store.append(
            "event",
            {
                "type": "tool_result",
                "data": {
                    "tool_use_id": "hist-call",
                    "content": "coverage search result echo",
                },
            },
        )

        history = SessionHistory(sessions_dir, project_root=tmp_path)
        history.set_session_id(store.session_id)
        tool = build_history_tools(history)[0]

        # 搜索 coverage：应该只返回真实的 bash 结果，不包含 history 回显
        output = str(tool.handler({"operation": "search", "query": "coverage"}))
        assert "All tests passed successfully" in output
        assert "coverage search result echo" not in output

    def test_search_large_content_produces_excerpt_and_offset(
        self, tmp_path: Path
    ) -> None:
        """当命中记录超过 2000 字符时，返回包含 match_offset 的上下文摘录。"""
        sessions_dir = tmp_path / ".cade" / "sessions"
        store = SessionStore(sessions_dir, project_root=tmp_path)

        prefix = "A" * 3000
        target = "UNIQUE_TARGET_NEEDLE"
        suffix = "B" * 3000
        long_content = f"{prefix} {target} {suffix}"

        entry_id = store.append(
            "event",
            {
                "type": "tool_result",
                "data": {"tool_use_id": "call-large", "content": long_content},
            },
        )

        history = SessionHistory(sessions_dir, project_root=tmp_path)
        history.set_session_id(store.session_id)
        tool = build_history_tools(history)[0]

        output = str(tool.handler({"operation": "search", "query": target}))
        assert f"entry_id={entry_id}" in output
        assert "match_offset=" in output
        assert "excerpt_offset=" in output
        assert target in output

    def test_read_pagination_contract(self, tmp_path: Path) -> None:
        """验证 read 分页按 offset 和 max_chars 精确截断，并指出 next_offset。"""
        sessions_dir = tmp_path / ".cade" / "sessions"
        store = SessionStore(sessions_dir, project_root=tmp_path)
        full_text = "0123456789" * 10  # 100 字符
        entry_id = store.append("user", full_text)

        history = SessionHistory(sessions_dir, project_root=tmp_path)
        history.set_session_id(store.session_id)
        tool = build_history_tools(history)[0]

        # 读取前 40 字符
        page1 = str(
            tool.handler(
                {
                    "operation": "read",
                    "entry_id": entry_id,
                    "offset": 0,
                    "max_chars": 40,
                }
            )
        )
        assert "offset=0" in page1
        assert "total_chars=100" in page1
        assert "next_offset=40" in page1
        assert full_text[:40] in page1

        # 读取后 60 字符到末尾
        page2 = str(
            tool.handler(
                {
                    "operation": "read",
                    "entry_id": entry_id,
                    "offset": 40,
                    "max_chars": 80,
                }
            )
        )
        assert "offset=40" in page2
        assert "next_offset=end" in page2
        assert full_text[40:] in page2

    def test_around_session_id_with_after_is_rejected(self, tmp_path: Path) -> None:
        """验证跨 session 查看邻域时若提供 after 参数必须报错（跨 session 只允许回溯祖先）。"""
        sessions_dir = tmp_path / ".cade" / "sessions"
        store = SessionStore(sessions_dir, project_root=tmp_path)
        eid = store.append("user", "step 1")

        history = SessionHistory(sessions_dir, project_root=tmp_path)
        tool = build_history_tools(history)[0]

        with pytest.raises(
            ValueError, match="after is only supported on the current branch"
        ):
            tool.handler(
                {
                    "operation": "around",
                    "entry_id": eid,
                    "session_id": store.session_id,
                    "after": 2,
                }
            )

    def test_symlink_escape_is_strictly_rejected(self, tmp_path: Path) -> None:
        """验证如果 session 文件是软链接指向外部存储目录，拒绝访问并报错。"""
        sessions_dir = tmp_path / ".cade" / "sessions"
        sessions_dir.mkdir(parents=True)
        outside_dir = tmp_path / "outside_sessions"
        outside_dir.mkdir(parents=True)

        outside_file = outside_dir / "session-evil.jsonl"
        outside_file.write_text(
            '{"id":"1","type":"user","content":"evil"}\n', encoding="utf-8"
        )

        symlink_file = sessions_dir / "session-symlinked.jsonl"
        try:
            symlink_file.symlink_to(outside_file)
        except OSError as exc:
            if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
                pytest.skip("The current Windows account cannot create symbolic links")
            raise

        history = SessionHistory(sessions_dir, project_root=tmp_path)
        with pytest.raises(ValueError, match="History session path escapes its store"):
            history.get_entry("symlinked", "1")
