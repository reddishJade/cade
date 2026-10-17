"""保护代码库内容检索 (grep) 与文件名匹配 (glob) 的搜索工具契约。

控制性回归使用真实 build_grep_tool 与 build_glob_tools。
在独立 tmp_path 空间断言正则匹配、大小写敏感性、路径限定以及敏感目录过滤。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cade.coding_agent.tools.glob_search import build_glob_tools
from cade.coding_agent.tools.grep_search import build_grep_tool


def test_glob_tool_pattern_matching_and_directory_filtering(tmp_path: Path) -> None:
    """验证 glob 工具能够匹配符合通配符的文件，并自动跳过 .git 等受保护目录。"""
    # 构造测试文件树
    src_dir = tmp_path / "src" / "pkg"
    src_dir.mkdir(parents=True)
    (src_dir / "app.py").write_text("# main app", encoding="utf-8")
    (src_dir / "utils.py").write_text("# utils", encoding="utf-8")
    (src_dir / "readme.txt").write_text("info", encoding="utf-8")

    # 构造敏感受保护目录中的文件
    git_dir = tmp_path / ".git" / "hooks"
    git_dir.mkdir(parents=True)
    (git_dir / "pre-commit.py").write_text("# hook", encoding="utf-8")

    glob_specs = build_glob_tools(project_root=tmp_path)
    glob_tool = next(t for t in glob_specs if t.name == "glob")

    # 1. 搜索所有 .py 文件
    output = glob_tool.handler({"pattern": "**/*.py"}, None)
    assert "src/pkg/app.py" in output
    assert "src/pkg/utils.py" in output
    assert "readme.txt" not in output
    # 敏感目录下的 .py 文件必须被自动阻断过滤
    assert ".git" not in output

    # 2. 局部子目录搜索
    sub_output = glob_tool.handler({"path": "src/pkg", "pattern": "*.txt"}, None)
    assert "readme.txt" in sub_output
    assert "app.py" not in sub_output


def test_grep_tool_content_search_and_case_sensitivity(tmp_path: Path) -> None:
    """验证 grep 内容搜索工具支持正则匹配、大小写配置与行号定位。"""
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir(parents=True)
    (docs_dir / "guide.md").write_text(
        "Line 1: System overview\n"
        "Line 2: CRITICAL_ERROR: failed to connect\n"
        "Line 3: Normal termination\n",
        encoding="utf-8",
    )
    (docs_dir / "notes.md").write_text(
        "Line 1: critical_error in lowercase\n",
        encoding="utf-8",
    )

    grep_spec = build_grep_tool(project_root=tmp_path)

    # 1. 精确匹配大写 CRITICAL_ERROR
    exact_res = grep_spec.handler({"pattern": r"CRITICAL_ERROR.*connect"}, None)
    assert "guide.md" in exact_res
    assert "Line 2" in exact_res
    assert "notes.md" not in exact_res

    # 2. 启用 ignore_case 匹配全部大小写变体
    case_res = grep_spec.handler(
        {"pattern": "critical_error", "ignore_case": True}, None
    )
    assert "guide.md" in case_res
    assert "notes.md" in case_res

    # 3. 未找到任何匹配项时的友好提示
    unmatched_res = grep_spec.handler({"pattern": "nonexistent_pattern_xyz"}, None)
    assert (
        "No matches found" in unmatched_res
        or "0 matches" in unmatched_res.lower()
        or not unmatched_res.strip()
    )


def test_grep_tool_blocked_path_protection(tmp_path: Path) -> None:
    """验证对越界或受阻断路径调用 grep 时直接拒绝报错。"""
    grep_spec = build_grep_tool(project_root=tmp_path)

    with pytest.raises(ValueError, match="path is blocked"):
        grep_spec.handler({"path": ".env", "pattern": "SECRET"}, None)

    with pytest.raises(ValueError, match="parent-directory paths are not allowed"):
        grep_spec.handler({"path": "../../outside", "pattern": "test"}, None)
