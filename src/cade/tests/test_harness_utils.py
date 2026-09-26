"""权限模型工具函数单元测试。"""

from __future__ import annotations

from cade.harness.security.permission_model.utils import (
    _looks_absolute,
    access_satisfies,
    command_grant_pattern,
    is_blocked_workspace_path,
    is_git_path,
    is_sensitive_path,
)


class TestCommandGrantPattern:
    def test_simple_command(self) -> None:
        result = command_grant_pattern("ls -la")
        assert result == "ls *"

    def test_git_subcommand(self) -> None:
        result = command_grant_pattern("git push origin main")
        assert result == "git push *"

    def test_npm_run(self) -> None:
        result = command_grant_pattern("npm run build --production")
        assert result == "npm run build *"


class TestLooksAbsolute:
    def test_unix_path(self) -> None:
        assert _looks_absolute("/etc/passwd")

    def test_windows_path(self) -> None:
        assert _looks_absolute("C:/Users/name")

    def test_relative(self) -> None:
        assert not _looks_absolute("src/main.py")

    def test_empty(self) -> None:
        assert not _looks_absolute("")


class TestIsSensitivePath:
    def test_dotenv(self) -> None:
        assert is_sensitive_path(".env")
        assert is_sensitive_path(".env.production")

    def test_dotenv_example_write_only(self) -> None:
        assert is_sensitive_path(".env.example", access="write")
        assert not is_sensitive_path(".env.example", access="read")

    def test_credential_paths(self) -> None:
        assert is_sensitive_path(".ssh/id_rsa")
        assert is_sensitive_path(".aws/config")


class TestIsBlockedWorkspacePath:
    def test_venv(self) -> None:
        assert is_blocked_workspace_path(".venv/lib/python")

    def test_pycache(self) -> None:
        assert is_blocked_workspace_path("src/__pycache__/foo.pyc")

    def test_normal_path(self) -> None:
        assert not is_blocked_workspace_path("src/main.py")


class TestIsGitPath:
    def test_dotgit(self) -> None:
        assert is_git_path(".git/config")

    def test_non_git(self) -> None:
        assert not is_git_path("src/main.py")


class TestAccessSatisfies:
    def test_read_write_covers_all(self) -> None:
        assert access_satisfies("read_write", "read")
        assert access_satisfies("read_write", "write")

    def test_read_only_read(self) -> None:
        assert access_satisfies("read", "read")
        assert not access_satisfies("read", "write")

    def test_write_only_write(self) -> None:
        assert access_satisfies("write", "write")
        assert not access_satisfies("write", "read")
