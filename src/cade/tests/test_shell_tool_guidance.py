"""验证默认 Shell 选择及核心工具的模型可见指引。"""

from __future__ import annotations

from pathlib import Path

import pytest

from cade.agent.config import AgentContext
from cade.agent.context import ContextCollectorRegistry
from cade.agent.messages import SystemMessage, UserMessage
from cade.agent.request import DefaultRequestAssembler
from cade.coding_agent.tools import shell_adapter
from cade.coding_agent.tools.bash import build_bash_tool
from cade.coding_agent.tools.edit import build_edit_tool
from cade.coding_agent.tools.read import build_read_tool
from cade.coding_agent.tools.shell_adapter import ShellSpec, detect_shell
from cade.coding_agent.tools.write import build_write_tool
from cade.harness.agent_runtime.prompting import build_runtime_context_provider
from cade.harness.agent_runtime.prompting.builder import make_directory_context_section


def test_default_shell_prefers_bash_over_user_login_shell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """自动模式使用确定的 Bash；显式指定仍允许 Zsh。"""
    monkeypatch.setattr(shell_adapter.sys, "platform", "linux")
    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.setattr(
        shell_adapter.shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name in {"bash", "zsh", "sh"} else None,
    )

    assert detect_shell().name == "bash"
    assert detect_shell("zsh").name == "zsh"


def test_default_shell_uses_posix_sh_when_bash_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """缺少 Bash 时使用 POSIX sh。"""
    monkeypatch.setattr(shell_adapter.sys, "platform", "linux")
    monkeypatch.setattr(
        shell_adapter.shutil,
        "which",
        lambda name: "/bin/sh" if name == "sh" else None,
    )

    assert detect_shell().name == "sh"


def test_windows_default_prefers_powershell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows 自动模式优先使用可用的 PowerShell。"""
    monkeypatch.setattr(shell_adapter.sys, "platform", "win32")
    monkeypatch.setattr(
        shell_adapter.shutil,
        "which",
        lambda name: name if name in {"powershell", "bash", "cmd"} else None,
    )

    assert detect_shell().name == "powershell"


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        (
            ShellSpec("bash", ("bash", "-c"), "posix"),
            (),
        ),
        (
            ShellSpec("cmd", ("cmd", "/c"), "cmd"),
            (),
        ),
        (
            ShellSpec("pwsh", ("pwsh", "-Command"), "powershell", ps_kind="pwsh"),
            (),
        ),
        (
            ShellSpec(
                "powershell",
                ("powershell", "-Command"),
                "powershell",
                ps_kind="powershell",
            ),
            ("PowerShell 5.1 does not support `&&`; use `;` to separate commands.",),
        ),
    ],
)
def test_bash_guidance_contains_only_required_shell_constraint(
    tmp_path: Path,
    spec: ShellSpec,
    expected: tuple[str, ...],
) -> None:
    """Bash 工具指引不教授绕过文件工具的命令。"""
    tool = build_bash_tool(tmp_path, shell_spec=spec)
    assert tool.prompt_guidelines == expected


def test_tool_discipline_distinguishes_read_edit_write_and_bash(
    tmp_path: Path,
) -> None:
    """最终提示词明确工具分工，不包含竞争性的文件命令示例。"""
    spec = ShellSpec("bash", ("bash", "-c"), "posix")
    registry = (
        build_read_tool(tmp_path),
        build_edit_tool(tmp_path),
        build_write_tool(tmp_path),
        build_bash_tool(tmp_path, shell_spec=spec),
    )
    prompt = "\n".join(
        build_runtime_context_provider(tmp_path, shell_spec=spec)(registry)
    )

    assert "Use read to inspect files and directories." in prompt
    assert "Use edit for targeted changes to existing files." in prompt
    assert "Use write to create files or deliberately replace" in prompt
    assert "Use bash for shell execution" in prompt
    assert "Prefer dedicated file tools over equivalent shell commands." in prompt
    assert '<shell tool="bash" name="bash" syntax="posix" />' in prompt
    for example in ("cat file.txt", "Get-Content", "Common file commands", "cd src"):
        assert example not in prompt


def test_directory_changes_do_not_rewrite_system_prefix(tmp_path: Path) -> None:
    """目录事实必须保持新鲜，但新增文件不能改写此前的系统指令。"""
    provide = build_runtime_context_provider(tmp_path)
    before = provide(())
    (tmp_path / "new-module.py").write_text("VALUE = 1\n")
    assert provide(()) == before


def test_directory_snapshot_is_fresh_after_retained_history(tmp_path: Path) -> None:
    """目录条目保留在当前事实中，新增条目只改变历史之后的快照。"""
    provide = build_runtime_context_provider(tmp_path)
    context = AgentContext(
        request_prefix=[SystemMessage(content=part) for part in provide(())],
        messages=[UserMessage(content="Current task")],
    )
    registry = ContextCollectorRegistry()
    registry.register_section(make_directory_context_section(tmp_path))
    assembler = DefaultRequestAssembler(context_collectors=registry.freeze())
    first = assembler.assemble(context, current_step=1, options=None)
    (tmp_path / "new-module.py").write_text("VALUE = 1\n")
    second = assembler.assemble(context, current_step=2, options=None)
    assert first.wire_messages[:-1] == second.wire_messages[:-1]
    assert "new-module.py" not in str(first.wire_messages)
    assert "new-module.py" in str(second.wire_messages[-1])
    assert second.wire_messages[-1]["role"] == "user"
    assert context.messages == [UserMessage(content="Current task")]
