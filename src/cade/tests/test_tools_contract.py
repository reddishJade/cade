"""保护文本精确编辑引擎、编码/换行往返以及本地文件操作工具契约。

控制性回归使用实际 Read、Write、Edit、TextEdit 与 LocalFileSystem。
在 pytest 独立 tmp_path 空间验证真实文件创建、精确替换、无隐式格式化及边界拦截。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cade.agent.types import ToolOutput
from cade.coding_agent.tools.edit import edit_file_content
from cade.coding_agent.tools.read import read_file_content
from cade.coding_agent.tools.text_edit import (
    apply_text_replacement,
    detect_line_ending,
    normalize_to_lf,
    restore_line_endings,
    strip_bom,
)
from cade.coding_agent.tools.write import write_file_content
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


def test_core_tools_write_edit_and_read_lifecycle(tmp_path: Path) -> None:
    """验证真实文件系统上的写入、精确定位编辑及分页读取生命周期。"""
    fs = LocalFileSystem()
    target_rel = "nested/deep/module.txt"

    # 1. write_file_content 自动创建父目录并写入文件
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
        "wrote" in str(write_res).lower()
        or "created" in str(write_res).lower()
        or target_rel in str(write_res)
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
    assert "line TWO" in str(edit_res)

    # 3. read_file_content 分页读取（offset=2, limit=1）
    read_res = read_file_content(
        tmp_path,
        fs,
        {"path": target_rel, "offset": 2, "limit": 1},
    )
    assert "line TWO" in read_res
    assert "line 1" not in read_res


def test_core_tools_error_rejections(tmp_path: Path) -> None:
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
    assert isinstance(read_bin, ToolOutput)
    assert read_bin.is_error
    assert "Cannot read binary file" in read_bin


def test_write_and_edit_leave_python_formatting_untouched(tmp_path: Path) -> None:
    """确保文件工具只写入指定内容，且 Diff 对应磁盘内容。"""
    fs = LocalFileSystem()
    path = tmp_path / "sample.py"
    written = write_file_content(
        tmp_path, fs, {"path": "sample.py", "content": "x=  1\n"}
    )
    assert path.read_bytes() == b"x=  1\n"
    assert isinstance(written, ToolOutput)
    assert "wrote file" in str(written)

    edited = edit_file_content(
        tmp_path, fs, {"path": "sample.py", "old_text": "1", "new_text": "2"}
    )
    assert path.read_bytes() == b"x=  2\n"
    assert isinstance(edited, ToolOutput)
    patch = str(edited.metadata["patch"])
    assert "-x=  1" in patch
    assert "+x=  2" in patch


def test_edit_preserves_unrelated_mixed_line_endings(tmp_path: Path) -> None:
    """精确替换不改变其他行的换行符。"""
    fs = LocalFileSystem()
    path = tmp_path / "mixed.txt"
    path.write_bytes(b"first\r\nsecond\nthird\r\n")
    edit_file_content(
        tmp_path, fs, {"path": "mixed.txt", "old_text": "second", "new_text": "SECOND"}
    )
    assert path.read_bytes() == b"first\r\nSECOND\nthird\r\n"


def test_ambiguous_edit_keeps_file_unchanged(tmp_path: Path) -> None:
    """同名片段重复时拒绝编辑，并保留原文件。"""
    fs = LocalFileSystem()
    path = tmp_path / "ambiguous.txt"
    before = b"same\nsame\n"
    path.write_bytes(before)
    with pytest.raises(ValueError, match="Found multiple matches"):
        edit_file_content(
            tmp_path,
            fs,
            {"path": "ambiguous.txt", "old_text": "same", "new_text": "new"},
        )
    assert path.read_bytes() == before


def test_bash_rejects_invalid_workdir(tmp_path: Path) -> None:
    """非法工作目录必须显式报错，不能回退到项目根目录。"""
    from cade.coding_agent.tools.bash import _resolve_workdir

    with pytest.raises(ValueError, match="parent-directory paths are not allowed"):
        _resolve_workdir(tmp_path, "../other")


def test_edit_matches_mixed_line_endings_without_normalizing_file(
    tmp_path: Path,
) -> None:
    """跨 LF/CRLF 匹配时逐个保留原换行符及目标外字节。"""
    fs = LocalFileSystem()
    path = tmp_path / "mixed.txt"
    before = b"\xef\xbb\xbfhead\r\nfirst\nsecond\r\nthird\nfoot\r\n"
    expected = b"\xef\xbb\xbfhead\r\nFIRST\nSECOND\r\nTHIRD\nfoot\r\n"
    path.write_bytes(before)

    result = edit_file_content(
        tmp_path,
        fs,
        {
            "path": "mixed.txt",
            "old_text": "first\nsecond\nthird",
            "new_text": "FIRST\nSECOND\nTHIRD",
        },
    )

    assert path.read_bytes() == expected
    assert "replacements=1" in str(result)
    patch = str(result.metadata["patch"])
    assert "+FIRST\n" in patch
    assert "+SECOND\r\n" in patch


def test_edit_replace_all_keeps_each_occurrence_line_endings(tmp_path: Path) -> None:
    """不同换行风格的多处匹配分别继承各自的换行符。"""
    fs = LocalFileSystem()
    path = tmp_path / "repeated.txt"
    path.write_bytes(b"top\r\nitem\r\nvalue\nmid\nitem\nvalue\r\nend")

    result = edit_file_content(
        tmp_path,
        fs,
        {
            "path": "repeated.txt",
            "old_text": "item\nvalue",
            "new_text": "ITEM\nVALUE",
            "replace_all": True,
        },
    )

    assert path.read_bytes() == b"top\r\nITEM\r\nVALUE\nmid\nITEM\nVALUE\r\nend"
    assert "replacements=2" in str(result)


def test_edit_rejects_ambiguous_matches_with_different_endings(
    tmp_path: Path,
) -> None:
    """LF 和 CRLF 归一匹配后如存在多个候选，应拒绝且不写入。"""
    fs = LocalFileSystem()
    path = tmp_path / "ambiguous.txt"
    before = b"item\r\nvalue\nitem\nvalue"
    path.write_bytes(before)

    with pytest.raises(ValueError, match="Found multiple matches"):
        edit_file_content(
            tmp_path,
            fs,
            {
                "path": "ambiguous.txt",
                "old_text": "item\nvalue",
                "new_text": "ITEM\nVALUE",
            },
        )

    assert path.read_bytes() == before


def test_edit_uses_adjacent_line_ending_for_new_lines(tmp_path: Path) -> None:
    """目标原本不含换行符时，新换行符跟随相邻行而非整个文件。"""
    fs = LocalFileSystem()
    path = tmp_path / "insert.txt"
    path.write_bytes(b"crlf\r\nnext\neditable\nend\r\n")

    edit_file_content(
        tmp_path,
        fs,
        {"path": "insert.txt", "old_text": "editable", "new_text": "edit\nable"},
    )

    assert path.read_bytes() == b"crlf\r\nnext\nedit\nable\nend\r\n"


def test_edit_adds_lines_without_modifying_unmatched_line_endings(
    tmp_path: Path,
) -> None:
    """新增行遵循匹配区域的换行风格，其他行保持原样。"""
    fs = LocalFileSystem()
    path = tmp_path / "added.txt"
    path.write_bytes(b"head\nA\r\nB\nC\r\nend")

    edit_file_content(
        tmp_path,
        fs,
        {
            "path": "added.txt",
            "old_text": "A\nB\nC",
            "new_text": "A\nX\nY\nC",
        },
    )

    assert path.read_bytes() == b"head\nA\r\nX\nY\nC\r\nend"


def test_write_replaces_bytes_without_inheriting_bom(tmp_path: Path) -> None:
    """Write 写入精确内容，不继承旧文件 BOM，允许覆盖非文本旧内容。"""
    fs = LocalFileSystem()
    path = tmp_path / "raw.txt"
    path.write_bytes(b"\xef\xbb\xbfold")
    write_file_content(tmp_path, fs, {"path": "raw.txt", "content": "new"})
    assert path.read_bytes() == b"new"

    path.write_bytes(b"\xff\xfe\x00")
    write_file_content(tmp_path, fs, {"path": "raw.txt", "content": "hello"})
    assert path.read_bytes() == b"hello"

    write_file_content(tmp_path, fs, {"path": "raw.txt", "content": "\ufeffmark"})
    assert path.read_bytes() == b"\xef\xbb\xbfmark"


def test_file_mutation_lock_registry_releases_keys(tmp_path: Path) -> None:
    """同一路径串行执行，所有操作完成后不保留锁条目。"""
    import concurrent.futures
    import threading
    import time

    from cade.coding_agent.tools.file_mutation_queue import (
        _file_locks,
        with_file_mutation,
    )

    path = tmp_path / "shared.txt"
    active = 0
    largest = 0
    guard = threading.Lock()

    def mutate() -> None:
        nonlocal active, largest

        def critical() -> None:
            nonlocal active, largest
            with guard:
                active += 1
                largest = max(largest, active)
            time.sleep(0.005)
            with guard:
                active -= 1

        with_file_mutation(path, critical)

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda _: mutate(), range(12)))
    assert largest == 1
    assert path.resolve() not in _file_locks
