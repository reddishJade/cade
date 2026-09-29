"""真实 fd 文件发现与可选搜索工具的集成验证。"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from cade.coding_agent.tools import _search_utils
from cade.coding_agent.tools.glob_search import build_glob_tools
from cade.coding_agent.tools.tools_manager import get_tool_path


# 失效情形：fd 未被选择、忽略文件泄漏、隐藏路径泄漏、结果上限失效。
@pytest.mark.skipif(
    shutil.which("fd") is None and shutil.which("fdfind") is None,
    reason="fd is unavailable",
)
def test_fd_discovers_files_for_optional_search_tools(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / ".cade").mkdir()
    (tmp_path / "__pycache__").mkdir()
    for relative in (
        "root.py",
        "sub/nested.py",
        "fd_only.py",
        "ignored.py",
        ".cade/private.py",
        "__pycache__/cache.py",
    ):
        (tmp_path / relative).write_text("content", encoding="utf-8")
    (tmp_path / ".fdignore").write_text("fd_only.py\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("ignored.py\n", encoding="utf-8")

    discovered = _search_utils.enumerate_search_files(tmp_path, tmp_path)
    tools = {tool.name: tool for tool in build_glob_tools(tmp_path)}
    found = tools["find"].handler({"pattern": "*.py"}, None)
    globbed = tools["glob"].handler({"pattern": "*.py", "max_results": 1}, None)

    assert get_tool_path("fd") is not None
    assert [path.relative_to(tmp_path).as_posix() for path in discovered] == [
        "root.py",
        "sub/nested.py",
    ]
    assert str(found) == "root.py\nsub/nested.py"
    assert found.metadata == {"count": 2, "truncated": False}
    assert str(globbed) == "root.py\n... truncated"
    assert globbed.metadata == {"count": 2, "truncated": True}


# 失效情形：fd 不可用时文件发现中断，而不是回退到 rg。
def test_file_discovery_falls_back_to_rg_without_fd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "sample.py").write_text("content", encoding="utf-8")
    monkeypatch.setattr(_search_utils, "get_fd_path", lambda: None)

    discovered = _search_utils.enumerate_search_files(tmp_path, tmp_path)

    assert discovered == [tmp_path / "sample.py"]


# 失效情形：搜索根为单个文件时，fd 后端把该文件当作目录而漏掉结果。
@pytest.mark.skipif(
    shutil.which("fd") is None and shutil.which("fdfind") is None,
    reason="fd is unavailable",
)
def test_optional_search_accepts_file_path_with_fd(tmp_path: Path) -> None:
    (tmp_path / "sample.py").write_text("content", encoding="utf-8")
    tools = {tool.name: tool for tool in build_glob_tools(tmp_path)}

    result = tools["find"].handler({"path": "sample.py", "pattern": "*.py"}, None)

    assert str(result) == "sample.py"
