"""Shell 检测与适配纯函数单元测试。"""

from __future__ import annotations

from cade.coding_agent.tools.shell_adapter import (
    _KNOWN_SHELLS,
    build_shell_argv,
)


class TestBuildShellArgv:
    def test_powershell(self) -> None:
        argv = build_shell_argv(_KNOWN_SHELLS["powershell"], "Write-Host hi")
        assert "powershell" in argv[0]
        assert "Write-Host hi" in argv[-1]

    def test_cmd(self) -> None:
        argv = build_shell_argv(_KNOWN_SHELLS["cmd"], "dir")
        assert "cmd" in argv[0]
        assert "dir" in argv[-1]


class TestKnownShells:
    def test_fish_is_denied(self) -> None:
        assert _KNOWN_SHELLS["fish"].deny
