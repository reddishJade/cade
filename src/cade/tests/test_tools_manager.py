"""工具管理器纯函数单元测试。"""

from __future__ import annotations

from cade.coding_agent.tools.tools_manager import (
    ExternalToolDefinition,
    _resolve_tool_path,
    get_tool_path,
)


class TestResolveToolPath:
    def test_falls_back_through_candidates(self) -> None:
        def resolver(name: str) -> str | None:
            return "/usr/bin/foo" if name == "foo" else None

        result = _resolve_tool_path(
            ExternalToolDefinition(
                display_name="rg",
                candidate_names=("rg", "foo"),
            ),
            resolver,
        )
        assert result == "/usr/bin/foo"

    def test_fd_uses_fdfind_candidate_when_available(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "cade.coding_agent.tools.tools_manager.shutil.which",
            lambda name: "/usr/bin/fdfind" if name == "fdfind" else None,
        )

        assert get_tool_path("fd") == "/usr/bin/fdfind"
