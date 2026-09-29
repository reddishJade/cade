"""apply_patch 工具解析与纯函数单元测试。"""

from __future__ import annotations

import pytest

from cade.coding_agent.tools.apply_patch import (
    _find_anchor,
    _find_sequence,
    _header_path,
    _patch_text,
    build_apply_patch_tool,
    extract_patch_paths,
    parse_patch,
)
from cade.harness.execution_env import LocalFileSystem


class TestParsePatch:
    def test_add_file(self) -> None:
        patch = """*** Begin Patch
*** Add File: src/new.py
+print('hello')
*** End Patch"""
        hunks = parse_patch(patch)
        assert len(hunks) == 1
        assert hunks[0].kind == "add"
        assert hunks[0].path == "src/new.py"
        assert hunks[0].add_lines == ("print('hello')",)

    def test_delete_file(self) -> None:
        patch = """*** Begin Patch
*** Delete File: src/old.py
*** End Patch"""
        hunks = parse_patch(patch)
        assert len(hunks) == 1
        assert hunks[0].kind == "delete"
        assert hunks[0].path == "src/old.py"

    def test_update_file(self) -> None:
        patch = """*** Begin Patch
*** Update File: src/main.py
@@
-old line
+new line
*** End Patch"""
        hunks = parse_patch(patch)
        assert len(hunks) == 1
        assert hunks[0].kind == "update"

    def test_move_file(self) -> None:
        patch = """*** Begin Patch
*** Update File: src/old.py
*** Move to: src/new.py
*** End Patch"""
        hunks = parse_patch(patch)
        assert hunks[0].kind == "move"
        assert hunks[0].move_path == "src/new.py"

    def test_missing_begin_raises(self) -> None:
        with pytest.raises(ValueError, match="Begin Patch"):
            parse_patch("*** End Patch")

    def test_missing_end_raises(self) -> None:
        with pytest.raises(ValueError, match="End Patch"):
            parse_patch("*** Begin Patch")

    def test_empty_patch_raises(self) -> None:
        with pytest.raises(ValueError, match="rejected"):
            parse_patch("*** Begin Patch\n*** End Patch")


class TestHeaderPath:
    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            _header_path("*** Update File: ", "*** Update File: ")


class TestPatchText:
    def test_missing_raises(self) -> None:
        with pytest.raises(ValueError, match="patch_text"):
            _patch_text({})

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="patch_text"):
            _patch_text({"patch_text": ""})


class TestFindSequence:
    def test_found(self) -> None:
        lines = ["a", "b", "c", "d"]
        assert _find_sequence(lines, ("b", "c"), 0) == 1

    def test_not_found(self) -> None:
        assert _find_sequence(["a", "b"], ("c",), 0) is None

    def test_beyond_bounds_returns_none(self) -> None:
        assert _find_sequence(["a"], ("a",), 5) is None


class TestFindAnchor:
    def test_after_cursor(self) -> None:
        lines = ["a", "b", "c"]
        assert _find_anchor(lines, "b", 0) == 2

    def test_fallback_from_start(self) -> None:
        lines = ["a", "b", "c"]
        assert _find_anchor(lines, "b", 5) == 2

    def test_empty_anchor_returns_cursor(self) -> None:
        assert _find_anchor(["a"], "", 1) == 1


class TestExtractPatchPaths:
    def test_from_patch_text(self) -> None:
        data = {
            "patch_text": "*** Begin Patch\n*** Update File: src/a.py\n@@\n-old\n+new\n*** End Patch",
        }
        paths = extract_patch_paths(data)
        assert "src/a.py" in paths

    def test_from_paths_field(self) -> None:
        data = {"paths": ["src/a.py", "src/b.py"]}
        paths = extract_patch_paths(data)
        assert len(paths) == 2

    def test_deduplicates(self) -> None:
        data = {
            "paths": ["src/a.py"],
            "patch_text": "*** Begin Patch\n*** Update File: src/a.py\n*** End Patch",
        }
        paths = extract_patch_paths(data)
        assert len(paths) == 1

    def test_non_dict_returns_empty(self) -> None:
        assert extract_patch_paths("not a dict") == ()


class _FailOnWriteFileSystem(LocalFileSystem):
    def __init__(self, fail_on_write: int) -> None:
        self._fail_on_write = fail_on_write
        self._writes = 0

    def write_bytes(self, path, data) -> None:
        self._writes += 1
        if self._writes == self._fail_on_write:
            raise OSError("injected write failure")
        super().write_bytes(path, data)


def test_apply_patch_rolls_back_all_paths_after_mid_patch_failure(tmp_path) -> None:
    (tmp_path / "a.txt").write_text("old-a\n", encoding="utf-8")
    (tmp_path / "move.txt").write_text("old-move\n", encoding="utf-8")
    (tmp_path / "delete.txt").write_text("old-delete\n", encoding="utf-8")
    (tmp_path / "fail.txt").write_text("old-fail\n", encoding="utf-8")

    patch = """*** Begin Patch
*** Update File: a.txt
@@
-old-a
+new-a
*** Add File: nested/new.txt
+created
*** Update File: move.txt
*** Move to: moved.txt
@@
-old-move
+new-move
*** Delete File: delete.txt
*** Update File: fail.txt
@@
-old-fail
+new-fail
*** End Patch"""

    tool = build_apply_patch_tool(
        tmp_path,
        operations=_FailOnWriteFileSystem(fail_on_write=4),
    )

    with pytest.raises(RuntimeError, match="all affected paths were rolled back"):
        tool.handler({"patch_text": patch})

    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "old-a\n"
    assert (tmp_path / "move.txt").read_text(encoding="utf-8") == "old-move\n"
    assert (tmp_path / "delete.txt").read_text(encoding="utf-8") == "old-delete\n"
    assert (tmp_path / "fail.txt").read_text(encoding="utf-8") == "old-fail\n"
    assert not (tmp_path / "moved.txt").exists()
    assert not (tmp_path / "nested" / "new.txt").exists()
    assert not (tmp_path / "nested").exists()
