"""保护文本精确编辑引擎、编码/换行往返以及本地文件操作工具契约。

控制性回归使用真实 FileHandlers、TextEdit 与 LocalFileSystem。
在 pytest 独立 tmp_path 空间验证真实文件创建、块替换、格式化触发及边界拦截。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cade.agent.types import AgentToolResult, tool_content_text
from cade.coding_agent.tools.file_handlers import (
    edit_file_content,
    read_file_content,
    write_file_content,
)
from cade.coding_agent.tools.text_edit import (
    apply_text_replacement,
    detect_line_ending,
    normalize_to_lf,
    restore_line_endings,
    strip_bom,
)
from cade.harness.execution_env import LocalFileSystem


def test_apply_text_replacement_exactness_and_uniqueness() -> None:
    """验证文本替换引擎的精确定位、多处匹配防混淆保护与全量替换。"""
    content = "def foo():\n    return 42\n\ndef bar():\n    return 42\n"

    # 1. 唯一匹配成功替换
    replaced = apply_text_replacement(
        content,
        "def foo():\n    return 42",
        "def foo():\n    return 100",
    )
    assert "return 100" in replaced
    assert "def bar():\n    return 42" in replaced

    # 2. 存在多处匹配且未启用 replace_all 时，必须拒绝以防止改错位置
    with pytest.raises(ValueError, match="Found multiple matches for old_string"):
        apply_text_replacement(content, "return 42", "return 0", replace_all=False)

    # 3. 显式启用 replace_all 时，批量替换所有出现处
    all_replaced = apply_text_replacement(
        content, "return 42", "return 0", replace_all=True
    )
    assert all_replaced.count("return 0") == 2
    assert "return 42" not in all_replaced

    # 4. 未找到目标内容时报错
    with pytest.raises(ValueError, match="Could not find old_string"):
        apply_text_replacement(content, "non_existent", "new")

    # 5. 替换前后内容相同时拒绝无效操作
    with pytest.raises(ValueError, match="No changes to apply"):
        apply_text_replacement(content, "def foo():", "def foo():")

    # 6. 空 old_string 拒绝
    with pytest.raises(ValueError, match="old_string cannot be empty"):
        apply_text_replacement(content, "", "something")


def test_line_ending_and_bom_integrity() -> None:
    """验证 CRLF/LF 换行检测、统一 LF 编辑以及保存时原样还原和 BOM 保护。"""
    crlf_content = "alpha\r\nbeta\r\ngamma\r\n"
    lf_content = "alpha\nbeta\ngamma\n"

    assert detect_line_ending(crlf_content) == "\r\n"
    assert detect_line_ending(lf_content) == "\n"

    # 统一转换到 LF
    normalized = normalize_to_lf(crlf_content)
    assert "\r" not in normalized

    # 编辑完成后还原原行尾
    restored_crlf = restore_line_endings(normalized, "\r\n")
    assert restored_crlf == crlf_content

    # BOM 处理
    bom_str = "\ufeffhello world"
    bom, pure = strip_bom(bom_str)
    assert bom == "\ufeff"
    assert pure == "hello world"

    no_bom, original = strip_bom("hello world")
    assert no_bom == ""
    assert original == "hello world"


def test_file_handlers_write_edit_and_read_lifecycle(tmp_path: Path) -> None:
    """验证真实文件系统上的写入、精确定位编辑及分页读取生命周期。"""
    fs = LocalFileSystem()
    target_rel = "nested/deep/module.txt"

    # 1. write_file_content 自动创建父目录并原子写文件
    write_res = write_file_content(
        tmp_path,
        fs,
        {"path": target_rel, "content": "line 1\nline 2\nline 3\n"},
    )
    assert (tmp_path / target_rel).exists()
    assert (tmp_path / target_rel).read_text(
        encoding="utf-8"
    ) == "line 1\nline 2\nline 3\n"
    assert (
        "wrote" in tool_content_text(write_res.content).lower()
        or "created" in tool_content_text(write_res.content).lower()
        or target_rel in tool_content_text(write_res.content)
    )

    # 2. edit_file_content 精确替换中间行
    edit_res = edit_file_content(
        tmp_path,
        fs,
        {"path": target_rel, "old_text": "line 2", "new_text": "line TWO"},
    )
    assert (tmp_path / target_rel).read_text(
        encoding="utf-8"
    ) == "line 1\nline TWO\nline 3\n"
    assert "line TWO" in tool_content_text(edit_res.content)

    # 3. read_file_content 分页读取（offset=2, limit=1）
    read_res = read_file_content(
        tmp_path,
        fs,
        {"path": target_rel, "offset": 2, "limit": 1},
    )
    assert isinstance(read_res, AgentToolResult)
    assert "line TWO" in tool_content_text(read_res.content)
    assert "line 1" not in tool_content_text(read_res.content)


def test_file_handlers_error_rejections(tmp_path: Path) -> None:
    """验证文件处理器对非法路径、目录写冲突及越界读取的严格报错。"""
    fs = LocalFileSystem()

    # 1. 尝试覆盖已有目录报错
    sub_dir = tmp_path / "somedir"
    sub_dir.mkdir()
    with pytest.raises(ValueError, match="path is a directory"):
        write_file_content(tmp_path, fs, {"path": "somedir", "content": "hello"})

    # 2. 尝试编辑不存在的文件报错
    with pytest.raises(ValueError, match="not a file"):
        edit_file_content(
            tmp_path,
            fs,
            {"path": "nonexistent.txt", "old_text": "a", "new_text": "b"},
        )

    # 3. 读取空文件的非法 offset 报错
    empty_file = tmp_path / "empty.txt"
    empty_file.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="Offset 5 is out of range"):
        read_file_content(tmp_path, fs, {"path": "empty.txt", "offset": 5})

    # 4. 读取二进制文件安全返回错误输出而不是乱码或崩溃
    binary_file = tmp_path / "blob.bin"
    binary_file.write_bytes(b"\x00\x01\x02\x03\xff\xfe")
    read_bin = read_file_content(tmp_path, fs, {"path": "blob.bin"})
    assert isinstance(read_bin, AgentToolResult)
    assert read_bin.is_error
    assert "Cannot read binary file" in tool_content_text(read_bin.content)
