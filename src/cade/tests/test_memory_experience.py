"""Experience 写入门与稀疏提示的聚焦回归。

这些测试只覆盖 E2E 无法安全覆盖的故障模式：MEMORY.md 是持久用户数据，
`- evidence:` 曾被解析器静默删除（写完就没有溯源），而未通过校验的记录
一旦被消费，就等于把半个经验当成项目事实注入上下文。检索排序、提示措辞
等行为由 benchmarks/runners/run_memory.py 的三臂评测观察，不在此处断言。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cade.agent.context import (
    ContextBlockSource,
    ContextCollectionInput,
    ContextPriority,
)
from cade.agent.messages import ToolResultMessage
from cade.harness.memory import (
    MemoryHintCollector,
    MemoryManager,
    memory_write_rejection,
    parse_experience,
)
from cade.harness.memory.parsing import parse_memory_blocks
from cade.harness.memory.tools import build_memory_tools

_VALID_BODY = (
    "type: experience\n"
    "root_cause: directory-oriented discovery assumes traversal semantics\n"
    "fix: classify explicit file input before directory traversal\n"
    "applies_when: fd backend with an explicit single-file path\n"
    "anchors: {anchors}\n"
    "evidence: {evidence}\n"
)


def _project(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "fd.py").write_text("print('fd')\n", encoding="utf-8")
    (tmp_path / "src" / "other.py").write_text("print('other')\n", encoding="utf-8")
    return tmp_path


def _manager(project_root: Path) -> MemoryManager:
    return MemoryManager(project_root, user_memory_file=project_root / "user-memory.md")


def _block(title: str, body: str) -> str:
    return f"## {title}\n{body}"


def test_evidence_field_survives_parsing() -> None:
    """实现回归：evidence 行必须留在记录正文与检索文本里。"""
    records = parse_memory_blocks(
        _block(
            "fd single-file mismatch",
            _VALID_BODY.format(
                anchors="src/fd.py", evidence="commit=1a2b3c4; session=abc"
            ),
        ),
        layer="project",
    )
    assert len(records) == 1
    assert "evidence: commit=1a2b3c4; session=abc" in records[0].body
    assert "commit=1a2b3c4" in records[0].search_text


_PARAMS = [
    (
        "type: experience\nfix: f\napplies_when: a\nanchors: src/fd.py\nevidence: t=1\n",
        "root_cause is required",
    ),
    (
        _VALID_BODY.format(anchors="sym=Discovery", evidence="t=1"),
        "anchors must include at least one file or dir anchor",
    ),
    (
        _VALID_BODY.format(anchors="src/fd.py", evidence=" "),
        "evidence must record at least one pointer",
    ),
    (
        _VALID_BODY.format(anchors="src/ghost.py", evidence="t=1"),
        "anchor path not found in repository: src/ghost.py",
    ),
    (
        (
            "type: experience\nconfidence: 0.9\nroot_cause: r\nfix: f\n"
            "applies_when: a\nanchors: src/fd.py\nevidence: t=1\n"
        ),
        "retired governance fields are not allowed: confidence",
    ),
]


@pytest.mark.parametrize(("body", "reason"), _PARAMS)
def test_write_gate_rejects_incomplete_experience(
    tmp_path: Path, body: str, reason: str
) -> None:
    project_root = _project(tmp_path)
    rejection = memory_write_rejection(
        _block("candidate", body), layer="project", project_root=project_root
    )
    assert rejection is not None
    assert reason in rejection


def test_write_gate_accepts_valid_record_and_anchor_recall(tmp_path: Path) -> None:
    project_root = _project(tmp_path)
    manager = _manager(project_root)
    block = _block(
        "fd single-file mismatch",
        _VALID_BODY.format(anchors="src/fd.py", evidence="commit=1a2b3c4"),
    )
    assert (
        memory_write_rejection(block, layer="project", project_root=project_root)
        is None
    )
    assert manager.add_memory_block(block, layer="project") is True

    matched = manager.search_memory_records("", anchor="src/fd.py")
    assert [record.title for record in matched] == ["fd single-file mismatch"]
    assert parse_experience(matched[0]) is not None
    assert manager.search_memory_records("", anchor="src/other.py") == []

    # 未通过校验的同类记录不会被召回：校验 gate 的是消费，不是晋升。
    assert (
        manager.add_memory_block(
            _block(
                "ghost anchor",
                _VALID_BODY.format(anchors="src/ghost.py", evidence="t=1"),
            ),
            layer="project",
        )
        is False
    )
    assert manager.search_memory_records("", anchor="src/ghost.py") == []


def test_remember_stamps_provenance_without_extra_inference(tmp_path: Path) -> None:
    project_root = _project(tmp_path)
    manager = _manager(project_root)
    tools = {
        tool.name: tool
        for tool in build_memory_tools(manager, session_id_provider=lambda: "sess-1")
    }
    result = tools["remember"].handler(
        {
            "title": "fd single-file mismatch",
            "root_cause": "directory-oriented discovery assumes traversal semantics",
            "fix": "classify explicit file input before traversal",
            "applies_when": "fd backend with an explicit single-file path",
            "anchors": ["src/fd.py"],
            "evidence": ["test=python -m pytest tests/test_fd.py -q"],
        }
    )
    assert "Saved to" in result
    written = (project_root / "MEMORY.md").read_text(encoding="utf-8")
    assert "session=sess-1" in written
    assert "test=python -m pytest tests/test_fd.py -q" in written
    assert "| state=" in result


def _hint_inputs(
    project_root: Path,
    *,
    recent_files: tuple[str, ...],
    messages: list[object] | None = None,
) -> tuple[MemoryHintCollector, ContextCollectionInput]:
    manager = _manager(project_root)
    # 同一 test 内可多次调用：标题已存在时 add 返回 False，记录保持不变。
    manager.add_memory_block(
        _block(
            "fd single-file mismatch",
            _VALID_BODY.format(
                anchors="src/fd.py, err=NotADirectoryError", evidence="commit=1a2b3c4"
            ),
        ),
        layer="project",
    )
    collector = MemoryHintCollector(manager, lambda: recent_files)
    return collector, ContextCollectionInput(messages=messages or [])


def test_hint_requires_deterministic_match(tmp_path: Path) -> None:
    project_root = _project(tmp_path)
    collector, empty = _hint_inputs(project_root, recent_files=())
    assert collector.collect(empty) == []

    collector, unrelated = _hint_inputs(project_root, recent_files=("src/other.py",))
    assert collector.collect(unrelated) == []


def test_hint_fires_on_anchor_and_stays_a_pointer(tmp_path: Path) -> None:
    project_root = _project(tmp_path)
    collector, touched = _hint_inputs(project_root, recent_files=("src/fd.py",))
    blocks = collector.collect(touched)
    assert len(blocks) == 1
    block = blocks[0]
    assert block.source is ContextBlockSource.MEMORY
    assert block.priority is ContextPriority.LOW
    assert "fd single-file mismatch" in block.content
    assert "classify explicit file input before traversal" not in block.content


def test_hint_fires_on_verbatim_error_signature(tmp_path: Path) -> None:
    project_root = _project(tmp_path)
    messages = [
        ToolResultMessage(
            tool_call_id="call-1",
            tool_name="bash",
            content="NotADirectoryError: [Errno 20] Not a directory: 'src/fd.py'",
            is_error=True,
        )
    ]
    collector, failed = _hint_inputs(
        project_root, recent_files=(), messages=list(messages)
    )
    blocks = collector.collect(failed)
    assert len(blocks) == 1
    assert "fd single-file mismatch" in blocks[0].content


def test_hint_ignores_successful_output(tmp_path: Path) -> None:
    project_root = _project(tmp_path)
    messages = [
        ToolResultMessage(
            tool_call_id="call-1",
            tool_name="bash",
            content="NotADirectoryError: [Errno 20] Not a directory: 'src/fd.py'",
            is_error=False,
        )
    ]
    collector, succeeded = _hint_inputs(
        project_root, recent_files=(), messages=list(messages)
    )
    assert collector.collect(succeeded) == []
