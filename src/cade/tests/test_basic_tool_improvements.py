"""验证核心工具的批量编辑和有界进程输出。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from cade.coding_agent.modes.tui.approval import tool_preview_lines
from cade.coding_agent.tools.edit import build_edit_tool, edit_file_content
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
        "import sys; sys.stdout.buffer.write("
        "'start\\n'.encode() + '界'.encode() * 50000 + b'\\nEND')",
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
