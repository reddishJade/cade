"""外部搜索程序消失时回退到 Python 搜索。"""

from pathlib import Path

from cade.coding_agent.tools import _search_utils
from cade.coding_agent.tools.grep_search import build_grep_tool


def test_rg_path_refreshes_when_cached_executable_disappears(
    tmp_path: Path,
    monkeypatch,
) -> None:
    old_rg = tmp_path / "old-rg"
    old_rg.write_text("tool", encoding="utf-8")
    old_rg.chmod(0o755)
    new_rg = tmp_path / "new-rg"
    new_rg.write_text("tool", encoding="utf-8")
    new_rg.chmod(0o755)
    monkeypatch.setattr(_search_utils, "_RG_PATH", str(old_rg))
    monkeypatch.setattr(_search_utils, "_RG_CHECKED", True)
    monkeypatch.setattr(_search_utils, "get_tool_path", lambda _name: str(new_rg))

    old_rg.unlink()

    assert _search_utils.get_rg_path() == str(new_rg)


def test_grep_falls_back_if_rg_disappears_before_spawn(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (tmp_path / "sample.py").write_text("needle = True\n", encoding="utf-8")
    monkeypatch.setattr(
        _search_utils, "get_rg_path", lambda: str(tmp_path / "missing-rg")
    )

    result = build_grep_tool(tmp_path).handler({"pattern": "needle"}, None)

    assert "needle = True" in result
