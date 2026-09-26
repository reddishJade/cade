"""路径工具纯函数单元测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from cade.coding_agent.tools.path_utils import (
    display_path,
    is_binary_file,
    is_path_blocked,
    resolve_absolute_path,
    resolve_project_path,
)


class TestResolveProjectPath:
    def test_simple_relative(self, tmp_path: Path) -> None:
        result = resolve_project_path(tmp_path, "sub/file.txt")
        assert result == (tmp_path / "sub/file.txt").resolve()

    def test_absolute_outside_root_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            resolve_project_path(tmp_path, "/etc/passwd")

    def test_dotdot_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="parent-directory"):
            resolve_project_path(tmp_path, "../escape")

    def test_empty_defaults_to_root(self, tmp_path: Path) -> None:
        result = resolve_project_path(tmp_path, "")
        assert result == tmp_path.resolve()


class TestIsPathBlocked:
    def test_blocked_git(self, tmp_path: Path) -> None:
        p = tmp_path / ".git" / "config"
        assert is_path_blocked(tmp_path, p)

    def test_blocked_venv(self, tmp_path: Path) -> None:
        p = tmp_path / ".venv" / "bin" / "python"
        assert is_path_blocked(tmp_path, p)

    def test_blocked_pycache(self, tmp_path: Path) -> None:
        p = tmp_path / "src" / "__pycache__" / "foo.pyc"
        assert is_path_blocked(tmp_path, p)

    def test_normal_path_not_blocked(self, tmp_path: Path) -> None:
        p = tmp_path / "src" / "main.py"
        assert not is_path_blocked(tmp_path, p)

    def test_outside_root_blocked(self, tmp_path: Path) -> None:
        p = Path("/etc/passwd")
        assert is_path_blocked(tmp_path, p)


class TestResolveAbsolutePath:
    def test_relative_within_root(self, tmp_path: Path) -> None:
        result = resolve_absolute_path(tmp_path, "sub/file.txt")
        assert result == (tmp_path / "sub/file.txt").resolve()

    def test_absolute_path(self, tmp_path: Path) -> None:
        target = tmp_path / "file.txt"
        result = resolve_absolute_path(tmp_path, str(target))
        assert result == target.resolve()

    def test_dotdot_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="parent-directory"):
            resolve_absolute_path(tmp_path, "../escape")

    def test_empty_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="path is required"):
            resolve_absolute_path(tmp_path, "")

    def test_quotes_stripped(self, tmp_path: Path) -> None:
        result = resolve_absolute_path(tmp_path, '"file.txt"')
        assert result == (tmp_path / "file.txt").resolve()


class TestDisplayPath:
    def test_relative(self, tmp_path: Path) -> None:
        p = tmp_path / "src" / "main.py"
        result = display_path(tmp_path, p)
        assert ".." not in result
        assert result == "src/main.py"

    def test_outside_root(self) -> None:
        p = Path("/etc/passwd")
        result = display_path(Path("/home/project"), p)
        assert result == str(p)


class TestIsBinaryFile:
    def test_extension(self, tmp_path: Path) -> None:
        assert is_binary_file(tmp_path / "file.zip", b"")

    def test_null_byte(self) -> None:
        assert is_binary_file(Path("f.dat"), b"hello\x00world")

    def test_high_non_printable(self) -> None:
        assert is_binary_file(Path("f.dat"), b"\x01\x02\x03\x04" * 100)

    def test_text_not_binary(self) -> None:
        sample = b"hello world\n" * 100
        assert not is_binary_file(Path("f.txt"), sample)

    def test_empty_sample(self) -> None:
        assert not is_binary_file(Path("f.txt"), b"")
