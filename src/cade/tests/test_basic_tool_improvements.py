"""验证核心工具的批量编辑和有界进程输出。"""

from __future__ import annotations

import shutil
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from cade.coding_agent.modes.tui.approval import tool_preview_lines
from cade.coding_agent.tools.bash import build_bash_tool
from cade.coding_agent.tools.edit import build_edit_tool, edit_file_content
from cade.coding_agent.tools.shell_adapter import ShellSpec
from cade.harness.execution_env import LocalFileSystem, SubprocessShell


def test_batch_edit_preserves_mixed_endings_and_unmodified_bytes(
    tmp_path: Path,
) -> None:
    """同一次调用的多处替换都定位于原始内容。"""
    path = tmp_path / "mixed.txt"
    path.write_bytes(b"head\r\nA\r\nB\nmiddle\nC\nD\r\nend")
    result = edit_file_content(
        tmp_path,
        LocalFileSystem(),
        {
            "path": "mixed.txt",
            "edits": [
                {"old_text": "A\nB", "new_text": "AA\nBB"},
                {"old_text": "C\nD", "new_text": "CC\nDD"},
            ],
        },
    )

    assert path.read_bytes() == b"head\r\nAA\r\nBB\nmiddle\nCC\nDD\r\nend"
    assert "replacements=2" in result


def test_batch_edit_rejects_overlap_without_writing(tmp_path: Path) -> None:
    """重叠区域及缺失匹配均应在写入前报错。"""
    path = tmp_path / "overlap.txt"
    original = b"alpha beta gamma"
    path.write_bytes(original)
    changes = [
        {"old_text": "alpha beta", "new_text": "one"},
        {"old_text": "beta gamma", "new_text": "two"},
    ]
    with pytest.raises(ValueError, match="overlap"):
        edit_file_content(
            tmp_path, LocalFileSystem(), {"path": "overlap.txt", "edits": changes}
        )
    assert path.read_bytes() == original

    changes[1] = {"old_text": "absent", "new_text": "two"}
    with pytest.raises(ValueError, match="Could not find"):
        edit_file_content(
            tmp_path, LocalFileSystem(), {"path": "overlap.txt", "edits": changes}
        )
    assert path.read_bytes() == original


def test_batch_edit_schema_and_approval_preview(tmp_path: Path) -> None:
    """模型工具公开批量参数，审批预览显示每一项修改。"""
    tool = build_edit_tool(tmp_path)
    assert tool.schema is not None
    assert tool.schema["required"] == ["path", "edits"]
    args = {
        "path": str(tmp_path / "file.txt"),
        "edits": [
            {"old_text": "first", "new_text": "FIRST"},
            {"old_text": "second", "new_text": "SECOND"},
        ],
    }
    preview = tool_preview_lines(tool, args)
    assert any("Edit 1" in line for line in preview)
    assert any("Edit 2" in line for line in preview)
    assert any("SECOND" in line for line in preview)


def test_shell_streams_large_output_to_log(tmp_path: Path) -> None:
    """超大无换行输出不在内存堆积，完整日志仍可恢复。"""
    payload = ("start\n" + "界" * 50_000 + "\nEND").encode("utf-8")
    command = [
        sys.executable,
        "-c",
        (
            "import sys; sys.stdout.buffer.write("
            "'start\\n'.encode() + '界'.encode() * 50000 + b'\\nEND')"
        ),
    ]
    updates: list[str] = []
    result = SubprocessShell().run(
        command,
        cwd=tmp_path,
        timeout=30_000,
        on_progress=updates.append,
    )
    assert result.returncode == 0
    assert len(result.output.encode("utf-8")) <= 50 * 1024
    assert result.output.endswith("\nEND")
    assert result.total_output_lines == 3
    assert "".join(updates) == payload.decode("utf-8")
    assert result.full_output_path is not None
    log = Path(result.full_output_path)
    try:
        assert log.read_bytes() == payload
    finally:
        log.unlink()


def test_shell_short_output_needs_no_log(tmp_path: Path) -> None:
    """小输出直接返回完整文本，不创建日志文件。"""
    result = SubprocessShell().run(
        [sys.executable, "-c", "print('ready')"],
        cwd=tmp_path,
    )
    assert result.output == "ready\n"
    assert result.full_output_path is None
    assert result.total_output_lines == 1


@pytest.fixture
def zsh_workspace() -> Iterator[Path]:
    """在仓库内创建并清理 shell 工具的测试工作区。"""
    root = Path(__file__).resolve().parents[3] / "e2e-results"
    root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="zsh-contract-", dir=root) as directory:
        yield Path(directory)


def test_zsh_bash_uses_effective_tempdir_for_heredoc(
    zsh_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """自定义临时目录应覆盖 zsh 继承的不可用临时文件前缀。"""
    zsh = shutil.which("zsh")
    if zsh is None:
        pytest.skip("zsh is not installed")

    selected_tmpdir = zsh_workspace / "selected-tmp"
    selected_tmpdir.mkdir()
    monkeypatch.setenv("TMPDIR", str(selected_tmpdir))
    monkeypatch.setenv("TMPPREFIX", str(zsh_workspace / "unavailable" / "zsh"))
    monkeypatch.setenv("CADE_TEST_PASSTHROUGH", "preserved")
    monkeypatch.setattr(tempfile, "tempdir", None)

    tool = build_bash_tool(
        zsh_workspace,
        shell_spec=ShellSpec(
            name="zsh",
            command_prefix=(zsh, "-f", "-c"),
            syntax="posix",
        ),
        shell=SubprocessShell(),
    )
    result = tool.handler(
        {
            "command": (
                'printf "%s\\n" "$TMPPREFIX"\n'
                'printf "%s\\n" "$CADE_TEST_PASSTHROUGH"\n'
                "cat <<'EOF'\nheredoc works\nEOF"
            )
        },
        None,
    )

    assert result == f"{selected_tmpdir}/zsh\npreserved\nheredoc works\n"


def test_batch_edit_approval_marks_each_truncation_and_shows_complete_text(
    tmp_path: Path,
) -> None:
    """超长审批内容逐项标记，完整视图可还原原始替换文本。"""
    import json
    from threading import Event
    from unittest.mock import patch

    from cade.coding_agent.modes.tui.app import _CadeTui
    from cade.coding_agent.modes.tui.approval import full_edit_preview_lines
    from cade.coding_agent.modes.tui.state import _HitlRequest, _TuiState

    edits = [
        {"old_text": "a" * 500, "new_text": "b" * 501},
        {"old_text": "[red]\nline", "new_text": "改" * 201 + "\r\nend"},
        {"old_text": "x" * 200, "new_text": "y" * 200},
    ]
    args = {"path": "source.py", "edits": edits}
    tool = build_edit_tool(tmp_path)
    summary = tool_preview_lines(tool, args)
    assert any(
        "old_text truncated: showing 200 of 500 characters" in s for s in summary
    )
    assert any(
        "new_text truncated: showing 200 of 501 characters" in s for s in summary
    )
    assert any(
        "new_text truncated: showing 200 of 206 characters" in s for s in summary
    )
    assert sum("truncated:" in s for s in summary) == 3
    assert any("Press Ctrl+O to expand the complete edit text" in s for s in summary)

    full = full_edit_preview_lines(args)
    for item in edits:
        assert f"  {json.dumps(item['old_text'], ensure_ascii=False)}" in full
        assert f"  {json.dumps(item['new_text'], ensure_ascii=False)}" in full

    state = _TuiState()
    state.tool_collapsed = True
    request = _HitlRequest(
        tool_name="edit", preview=summary, event=Event(), full_preview=full
    )
    state.pending_hitl = request
    tui = object.__new__(_CadeTui)
    tui._state = state
    assert state.tool_collapsed
    with patch.object(_CadeTui, "_refresh"):
        tui._toggle_tools_key(None)
        assert not state.tool_collapsed
        assert f"  {full[3]}" in state._hitl_lines()
        tui._toggle_tools_key(None)
        assert state.tool_collapsed
        assert any("truncated" in line for line in state._hitl_lines())


def test_batch_edit_approval_exact_limit_is_not_truncated(tmp_path: Path) -> None:
    """恰好达到预览上限时不会错误报告内容被省略。"""
    args = {
        "path": "sample.txt",
        "edits": [{"old_text": "a" * 200, "new_text": "b" * 200}],
    }
    summary = tool_preview_lines(build_edit_tool(tmp_path), args)
    assert not any("truncated" in line for line in summary)
