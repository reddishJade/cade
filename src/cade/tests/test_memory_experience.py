"""Experience 记忆的聚焦回归。

只覆盖 E2E 无法安全覆盖的故障模式：MEMORY.md 是持久用户数据，手工编辑可以绕过
写入校验，所以"坏记录不得被当成正文消费"必须是读取期不变量；新鲜度必须只看内容
快照，否则"改完还没提交"这一最常见的工作流会立刻得到一个错误的 state。

检索排序、提示措辞、benchmark 三臂效果由 benchmarks/runners/run_memory.py 观察。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cade.agent.context import (
    ContextBlockSource,
    ContextCollectionInput,
    ContextPriority,
)
from cade.agent.messages import SystemMessage
from cade.coding_agent.validation import latest_validation_evidence
from cade.harness.memory import (
    MemoryHintCollector,
    MemoryHintState,
    MemoryManager,
    ValidationEvidence,
    anchor_freshness,
    anchor_snapshot,
    experience_status,
    memory_write_rejection,
    parse_experience,
    select_hints,
)
from cade.harness.memory.parsing import parse_memory_blocks
from cade.harness.memory.tools import build_memory_tools

_BODY = (
    "type: experience\n"
    "root_cause: directory-oriented discovery assumes traversal semantics\n"
    "fix: classify explicit file input before directory traversal\n"
    "applies_when: fd backend with an explicit single-file path\n"
    "anchors: {anchors}\n"
    "evidence: {evidence}\n"
)


def _project(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "src" / "fd.py").write_text("print('fd')\n", encoding="utf-8")
    (tmp_path / "src" / "other.py").write_text("print('other')\n", encoding="utf-8")
    return tmp_path


def _manager(root: Path) -> MemoryManager:
    return MemoryManager(root, user_memory_file=root / "user-memory.md")


def _block(
    title: str,
    *,
    anchors: str = "src/fd.py",
    evidence: str = "session=s1; validation=abc123",
) -> str:
    return f"## {title}\n{_BODY.format(anchors=anchors, evidence=evidence)}"


def _write(root: Path, block: str) -> None:
    """把一段完整 H2 记录追加到 MEMORY.md，模拟用户手工编辑。"""
    path = root / "MEMORY.md"
    current = (
        path.read_text(encoding="utf-8") if path.is_file() else "# Project memory\n"
    )
    path.write_text(f"{current}\n{block.strip()}\n", encoding="utf-8")


def test_evidence_field_survives_parsing() -> None:
    """实现回归：evidence 行必须留在正文与检索文本里。"""
    records = parse_memory_blocks(
        _block("fd mismatch", evidence="session=s1; validation=abc123"), layer="project"
    )
    assert "validation=abc123" in records[0].body
    assert "validation=abc123" in records[0].search_text


def test_invalid_experience_is_never_consumed_as_content(tmp_path: Path) -> None:
    root = _project(tmp_path)
    manager = _manager(root)
    _write(
        root,
        "## hand written broken\ntype: experience\nroot_cause: guess\n",
    )
    record = manager.read_memory_records("project")[0]
    assert experience_status(record).tier == "invalid"

    index = manager.render_index_result(record)
    assert "INVALID" in index and "fix is required" in index
    assert "guess" not in manager.render_full_result(record)
    packet = manager.render_prompt_packet(record)
    assert "guess" not in packet and "Fix MEMORY.md" in packet
    assert select_hints(manager, recent_files=("src/fd.py",), recent_text="") == ()


def test_evidence_tier_distinguishes_event_from_claim(tmp_path: Path) -> None:
    root = _project(tmp_path)
    manager = _manager(root)
    _write(root, _block("claimed lesson", evidence="test=made up"))
    _write(root, _block("recorded lesson"))
    tiers = {
        record.title: experience_status(record).tier
        for record in manager.read_memory_records("project")
    }
    assert tiers == {"claimed lesson": "claim", "recorded lesson": "event"}


def test_freshness_uses_content_snapshot_not_git(tmp_path: Path) -> None:
    root = _project(tmp_path)
    manager = _manager(root)
    snapshot = anchor_snapshot(("src/fd.py",), root)
    assert snapshot is not None
    _write(
        root,
        _block("snapshot lesson", evidence=f"session=s1; anchor_state={snapshot}"),
    )
    record = manager.read_memory_records("project")[0]
    experience = parse_experience(record)
    assert experience is not None
    assert anchor_freshness(experience, root) == "unchanged"

    # 真实工作流：改完文件但还没提交，快照仍然说明锚点变了 → changed。
    (root / "src" / "fd.py").write_text("print('fixed')\n", encoding="utf-8")
    assert anchor_freshness(experience, root) == "changed"

    # 锚点被删除必须报 missing，而不是"没变化"。
    (root / "src" / "fd.py").unlink()
    assert anchor_freshness(experience, root) == "missing"

    # 没有快照的历史记录只能 unknown。
    _write(root, _block("no snapshot", evidence="session=s1"))
    other = parse_experience(
        next(
            record
            for record in manager.read_memory_records("project")
            if record.title == "no snapshot"
        )
    )
    assert other is not None
    assert anchor_freshness(other, root) == "unknown"


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (
            (
                "type: experience\nfix: f\napplies_when: a\nanchors: src/fd.py\n"
                "evidence: session=s1\n"
            ),
            "root_cause is required",
        ),
        (
            _BODY.format(anchors=" ", evidence="session=s1"),
            "anchors must list at least one anchor",
        ),
        (
            _BODY.format(anchors="src/fd.py", evidence=" "),
            "evidence must record at least one pointer",
        ),
        (
            _BODY.format(anchors="src/ghost.py", evidence="session=s1"),
            "anchor path not found in repository: src/ghost.py",
        ),
        (
            (
                "type: experience\nconfidence: 0.9\nroot_cause: r\nfix: f\n"
                "applies_when: a\nanchors: src/fd.py\nevidence: session=s1\n"
            ),
            "retired governance fields are not allowed: confidence",
        ),
    ],
)
def test_write_gate_rejects_incomplete_experience(
    tmp_path: Path, body: str, reason: str
) -> None:
    root = _project(tmp_path)
    rejection = memory_write_rejection(
        f"## candidate\n{body}", layer="project", project_root=root
    )
    assert rejection is not None
    assert reason in rejection


def test_experience_is_project_scoped(tmp_path: Path) -> None:
    root = _project(tmp_path)
    rejection = memory_write_rejection(
        _block("user scoped"), layer="user", project_root=root
    )
    assert rejection is not None
    assert "project-scoped" in rejection


def test_error_only_anchor_is_accepted_and_recall_only(tmp_path: Path) -> None:
    """没有路径锚点的经验仍然值得保存，只是不会被路径提示触发。"""
    root = _project(tmp_path)
    manager = _manager(root)
    block = _block("cuda oom", anchors="err=CUDA out of memory", evidence="session=s1")
    assert memory_write_rejection(block, layer="project", project_root=root) is None
    assert manager.add_memory_block(block, layer="project") is True
    assert select_hints(manager, recent_files=("src/fd.py",), recent_text="") == ()
    hinted = select_hints(
        manager, recent_files=(), recent_text="boom: CUDA out of memory at step 3"
    )
    assert [hint.experience.problem for hint in hinted] == ["cuda oom"]

    symbol_only = _block(
        "symbol lesson", anchors="sym=ProviderClient", evidence="session=s1"
    )
    assert (
        memory_write_rejection(symbol_only, layer="project", project_root=root) is None
    )
    manager.add_memory_block(symbol_only, layer="project")
    assert (
        select_hints(
            manager, recent_files=("src/fd.py",), recent_text="ProviderClient failed"
        )
        == ()
    )


def test_hint_is_one_shot_per_window_and_after_explicit_recall(tmp_path: Path) -> None:
    root = _project(tmp_path)
    manager = _manager(root)
    manager.add_memory_block(_block("fd mismatch"), layer="project")
    state = MemoryHintState()
    collector = MemoryHintCollector(manager, lambda: ("src/fd.py",), state=state)

    first = collector.collect(ContextCollectionInput(messages=[]))
    assert len(first) == 1
    assert first[0].source is ContextBlockSource.MEMORY
    assert first[0].priority is ContextPriority.LOW
    assert "classify explicit file input" not in first[0].content
    assert collector.collect(ContextCollectionInput(messages=[])) == []

    # 新窗口重置去重状态：换窗后允许再提示一次。
    window = SystemMessage(
        content='<context-window-reset id="w2">replacement</context-window-reset>'
    )
    assert len(collector.collect(ContextCollectionInput(messages=[window]))) == 1

    # 显式 recall 之后不再自动提示。
    tools = {tool.name: tool for tool in build_memory_tools(manager, hint_state=state)}
    index = tools["recall"].handler({"anchor": "src/fd.py"})
    memory_id = index.split()[1]
    assert "Index only" in index
    assert "classify explicit file input" not in index
    full = tools["recall"].handler({"memory_id": memory_id})
    assert "classify explicit file input" in full
    assert collector.collect(ContextCollectionInput(messages=[window])) == []


def test_exact_file_anchor_beats_broad_directory_anchors(tmp_path: Path) -> None:
    """宽锚点不得因为出现在文件前面就挤掉精确锚点。"""
    root = _project(tmp_path)
    manager = _manager(root)
    for index in range(12):
        manager.add_memory_block(
            _block(f"broad {index}", anchors="dir=src", evidence="session=s1"),
            layer="project",
        )
    manager.add_memory_block(
        _block("exact file lesson", anchors="src/fd.py", evidence="session=s1"),
        layer="project",
    )
    hints = select_hints(manager, recent_files=("src/fd.py",), recent_text="")
    assert hints[0].experience.problem == "exact file lesson"
    assert hints[0].matched.kind == "file"


def test_remember_requires_a_recorded_validation_event(tmp_path: Path) -> None:
    root = _project(tmp_path)
    manager = _manager(root)
    tools = {
        tool.name: tool
        for tool in build_memory_tools(
            manager,
            session_id_provider=lambda: "sess-1",
            validation_provider=lambda: None,
        )
    }
    rejected = tools["remember"].handler(
        {
            "title": "fd mismatch",
            "root_cause": "r",
            "fix": "f",
            "applies_when": "a",
            "anchors": ["src/fd.py"],
        }
    )
    assert "No successful validation event" in rejected
    assert not (root / "MEMORY.md").exists()


def _validation_entry(
    entry_id: str, *, purpose: str | None, exit_code: object
) -> object:
    metadata: dict[str, object] = {"exit_code": exit_code}
    if purpose is not None:
        metadata["purpose"] = purpose
        metadata["validation_state"] = {"before": "a", "after": "a"}
    return _Entry(
        entry_id,
        {
            "type": "tool_result",
            "data": {
                "tool_use_id": f"call-{entry_id}",
                "metadata": metadata,
                "render_intent": {"kind": "terminal", "command": "pytest -q"},
                "content": "output",
            },
        },
    )


class _Entry:
    def __init__(self, entry_id: str, content: object) -> None:
        self.id = entry_id
        self.content = content


class _FakeStore:
    def __init__(self, entries: list[object]) -> None:
        self._entries = entries

    def build_branch(self) -> list[object]:
        return self._entries


@pytest.mark.parametrize(
    ("entries", "expected"),
    [
        ([_validation_entry("plain", purpose=None, exit_code=0)], None),
        ([_validation_entry("failed", purpose="validation", exit_code=1)], None),
        (
            [
                _validation_entry("ok", purpose="validation", exit_code=0),
                _validation_entry("later-failed", purpose="validation", exit_code=1),
            ],
            None,
        ),
        (
            [_validation_entry("ok", purpose="validation", exit_code=0)],
            "ok",
        ),
    ],
)
def test_evidence_requires_the_latest_validation_to_succeed(
    entries: list[object], expected: str | None
) -> None:
    """最近一次显式验证失败时不得回退到更早的成功，否则等于伪造"刚刚验证过"。"""
    evidence = latest_validation_evidence(_FakeStore(entries))
    assert (evidence.message_id if evidence else None) == expected


def test_remember_stamps_host_evidence(tmp_path: Path) -> None:
    root = _project(tmp_path)
    manager = _manager(root)
    evidence = ValidationEvidence(
        message_id="entry-1",
        command="python -m pytest tests; rm -rf /",
        exit_code=0,
        file_state="unchanged",
    )
    tools = {
        tool.name: tool
        for tool in build_memory_tools(
            manager,
            session_id_provider=lambda: "sess-1",
            validation_provider=lambda: evidence,
        )
    }
    result = tools["remember"].handler(
        {
            "title": "fd mismatch",
            "root_cause": "directory-oriented discovery",
            "fix": "classify explicit file input",
            "applies_when": "single-file path",
            "anchors": ["src/fd.py"],
        }
    )
    assert "Saved to" in result
    written = (root / "MEMORY.md").read_text(encoding="utf-8")
    assert "validation=entry-1" in written
    assert "session=sess-1" in written
    assert "anchor_state=sha256:" in written
    # 命令里的 `;` 会破坏证据分隔，必须被压平。
    assert "tests rm -rf /" in written
    assert "tests; rm" not in written
    record = manager.read_memory_records("project")[0]
    assert experience_status(record).tier == "event"
