"""上下文检索状态单元测试。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from cade.harness.agent_runtime.contextual import ContextualRetrievalState


class TestContextualRetrievalState:
    def test_max_files_lru(self) -> None:
        state = ContextualRetrievalState(Path("/project"), max_files=3)
        for i in range(5):
            state.record_file(Path(f"file{i}.py"))
        rendered = state.render()
        assert "file0.py" not in rendered
        assert "file4.py" in rendered

    def test_record_tool_result(self) -> None:
        state = ContextualRetrievalState(Path("/project"))
        state.record_tool_result("read", "loaded 42 lines of code")
        rendered = state.render()
        assert "read" in rendered
        assert "recent_tool_results" in rendered

    def test_record_tool_call(self) -> None:
        state = ContextualRetrievalState(Path("/project"))
        state.record_tool_call(tool="write", input_brief="path=/x", status="allow")
        rendered = state.render()
        assert "recent_tool_calls" in rendered
        assert "write" in rendered

    def test_cache_invalidation_on_record(self) -> None:
        state = ContextualRetrievalState(Path("/project"))
        first = state.render()
        state.record_file(Path("/project/file.py"))
        second = state.render()
        assert first != second

    def test_parallel_file_records_remain_unique(self) -> None:
        state = ContextualRetrievalState(Path("/project"), max_files=8)

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(
                pool.map(
                    lambda index: state.record_file(Path(f"src/file{index % 4}.py")),
                    range(200),
                )
            )

        assert len(state.recent_files) == 4
        assert set(state.recent_files) == {
            "src/file0.py",
            "src/file1.py",
            "src/file2.py",
            "src/file3.py",
        }
        assert state.active_file in state.recent_files

    def test_clear_removes_previous_session_projection(self) -> None:
        state = ContextualRetrievalState(Path("/project"))
        state.record_file(Path("src/main.py"))
        state.record_tool_result("read", "loaded content")

        state.clear()

        rendered = state.render()
        assert "src/main.py" not in rendered
        assert "loaded content" not in rendered
        assert state.active_file is None
