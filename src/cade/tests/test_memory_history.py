"""可提交的 Memory 文件冲突、来源存活和 workspace 隔离回归。

这些窄测试使用真实文件、SessionStore 和工具边界，不依赖 provider 或沙箱服务。
覆盖 footer 累积、导航 cache 丢失、共享历史越界和证据预览冒充原文的失效方式。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from cade.coding_agent.execution_modes import policy_for_mode
from cade.coding_agent.tools.bash import build_bash_tool
from cade.coding_agent.tools.grep_search import build_grep_tool
from cade.harness.memory import build_save_memory_tool
from cade.harness.session import SessionHistory, SessionStore, build_history_tools
from cade.harness.session.artifacts import offload_large_tool_result


def _history(store: SessionStore) -> SessionHistory:
    history = SessionHistory(
        store.sessions_dir,
        project_root=store.project_root,
        artifacts_dir=store.artifacts_dir,
    )
    history.set_session_id(store.session_id)
    return history


def test_save_creates_markdown_with_only_explicit_sources(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / ".cade/sessions", project_root=tmp_path)
    source = store.append("assistant", "observed failure")
    store.append("assistant", "unrelated observation")
    save = build_save_memory_tool(tmp_path, _history(store))
    directory = tmp_path / ".cade/memory"
    assert not directory.exists()
    save.handler(
        {
            "path": ".cade/memory/failure.md",
            "markdown": "Free-form explanation; its interpretation is not verified.",
            "sources": [{"entry_id": source}, {"entry_id": source}],
        },
        None,
    )
    text = (directory / "failure.md").read_text(encoding="utf-8")
    assert text.startswith("Free-form explanation;")
    assert text.count(f"session_id={store.session_id} entry_id={source}") == 1
    assert "unrelated observation" not in text


def test_full_text_updates_replace_only_the_host_footer(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / ".cade/sessions", project_root=tmp_path)
    first = store.append("assistant", "first evidence")
    save = build_save_memory_tool(tmp_path, _history(store))
    path = tmp_path / ".cade/memory/failure.md"
    body = "# Failure\n\nVersion 0\n\n## Sources\nAuthor-owned discussion."
    save.handler(
        {"path": str(path), "markdown": body, "sources": [{"entry_id": first}]},
        None,
    )
    for revision in (1, 2):
        previous = path.read_text(encoding="utf-8")
        source = store.append("assistant", f"revision {revision} evidence")
        save.handler(
            {
                "path": str(path),
                "markdown": previous.replace(
                    f"Version {revision - 1}", f"Version {revision}"
                ),
                "expected_content": previous,
                "sources": [{"entry_id": source}],
            },
            None,
        )
        updated = path.read_text(encoding="utf-8")
        assert "## Sources\nAuthor-owned discussion." in updated
        assert updated.count("<!-- cade:memory:sources -->") == 1
        assert updated.count("## Sources") == 2
        assert f"entry_id={source}" in updated
        assert f"entry_id={first}" not in updated
    with pytest.raises(ValueError, match="footer"):
        save.handler(
            {
                "path": str(path),
                "markdown": updated + "Text after the reserved footer.\n",
                "expected_content": updated,
                "sources": [{"entry_id": source}],
            },
            None,
        )
    assert path.read_text(encoding="utf-8") == updated


def test_conflict_and_missing_source_never_overwrite_memory(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / ".cade/sessions", project_root=tmp_path)
    source = store.append("assistant", "evidence")
    save = build_save_memory_tool(tmp_path, _history(store))
    path = tmp_path / ".cade/memory/failure.md"
    data = {
        "path": str(path),
        "markdown": "Original",
        "sources": [{"entry_id": source}],
    }
    save.handler(data, None)
    original = path.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="changed or already exists"):
        save.handler(data, None)
    path.write_text("Human edit", encoding="utf-8")
    with pytest.raises(ValueError, match="changed or already exists"):
        save.handler({**data, "expected_content": original}, None)
    assert path.read_text(encoding="utf-8") == "Human edit"
    with pytest.raises(ValueError, match="not found"):
        save.handler(
            {
                **data,
                "expected_content": "Human edit",
                "sources": [{"entry_id": "missing"}],
            },
            None,
        )
    assert path.read_text(encoding="utf-8") == "Human edit"


@pytest.mark.parametrize("cache", ["missing", "corrupt", "stale"])
def test_exact_read_and_ancestry_survive_navigation_cache_loss(
    tmp_path: Path, cache: str
) -> None:
    store = SessionStore(tmp_path / ".cade/sessions", project_root=tmp_path)
    root = store.append("assistant", "root observation")
    target = store.append("assistant", "abandoned evidence")
    store.append("assistant", "descendant must not leak")
    assert store.jump_to_entry(root)
    store.append("assistant", "alternate branch must not leak")
    source_session = store.session_id
    lines = store.current_path.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first.pop("project_path")
    lines[0] = json.dumps(first)
    store.current_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    store.clear()
    current = store.append("assistant", "current session")
    current_head = store.append("assistant", "later current observation")
    history = _history(store)
    if cache == "missing":
        store.index_path.unlink()
    else:
        store.index_path.write_text(
            "broken JSON" if cache == "corrupt" else '{"sessions": []}',
            encoding="utf-8",
        )
    cache_before = store.index_path.read_bytes() if store.index_path.exists() else None
    read = history.read(target, session_id=source_session)
    assert read is not None and read.content == "abandoned evidence"
    around = history.around(target, session_id=source_session, before=20)
    assert [entry.id for entry in around] == [root, target]
    tool = build_history_tools(history)[0]
    with pytest.raises(ValueError, match="after"):
        tool.handler(
            {
                "operation": "around",
                "session_id": source_session,
                "entry_id": target,
                "after": 1,
            },
            None,
        )
    assert [entry.id for entry in history.around(current, before=0, after=1)] == [
        current,
        current_head,
    ]
    assert history.session_id == store.session_id
    assert store.build_branch()[-1].id == current_head
    save = build_save_memory_tool(tmp_path, history)
    save.handler(
        {
            "path": ".cade/memory/recovered.md",
            "markdown": "Recovered evidence",
            "sources": [{"session_id": source_session, "entry_id": target}],
        },
        None,
    )
    if cache_before is None:
        assert not store.index_path.exists()
    else:
        assert store.index_path.read_bytes() == cache_before


@pytest.mark.parametrize("has_binding", [True, False])
def test_local_provenance_survives_workspace_relocation(
    tmp_path: Path, has_binding: bool
) -> None:
    original = tmp_path / "original"
    relocated = tmp_path / "relocated"
    store = SessionStore(original / ".cade/sessions", project_root=original)
    source = store.append("assistant", "observed before relocation")
    session = store.session_id
    if not has_binding:
        entry = json.loads(store.current_path.read_text(encoding="utf-8"))
        entry.pop("project_path")
        store.current_path.write_text(json.dumps(entry) + "\n", encoding="utf-8")
    save = build_save_memory_tool(original, _history(store))
    save.handler(
        {
            "path": ".cade/memory/relocation.md",
            "markdown": "Keep this observation across relocation.",
            "sources": [{"session_id": session, "entry_id": source}],
        },
        None,
    )
    log_before = store.current_path.read_bytes()
    store.index_path.unlink()
    original.rename(relocated)
    history = SessionHistory(relocated / ".cade/sessions", project_root=relocated)
    read = history.read(source, session_id=session)
    assert read is not None and read.content == "observed before relocation"
    assert [entry.id for entry in history.around(source, session_id=session)] == [
        source
    ]
    memory_path = relocated / ".cade/memory/relocation.md"
    previous = memory_path.read_text(encoding="utf-8")
    build_save_memory_tool(relocated, history).handler(
        {
            "path": str(memory_path),
            "markdown": previous.replace("Keep this", "Reuse this"),
            "expected_content": previous,
            "sources": [{"session_id": session, "entry_id": source}],
        },
        None,
    )
    assert memory_path.read_text(encoding="utf-8").startswith("Reuse this observation")
    assert (history.sessions_dir / store.current_path.name).read_bytes() == log_before
    assert not (relocated / ".cade/session_index.json").exists()


def test_external_history_uses_durable_workspace_binding(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    store = SessionStore(tmp_path / "shared/sessions", project_root=workspace)
    store.append("assistant", "root evidence")
    source = store.append("assistant", "target evidence")
    history = _history(store)
    full_fork = store.fork_into()
    partial_fork = store.fork_from_entry(source)
    foreign = SessionStore(store.sessions_dir, project_root=tmp_path / "other")
    foreign_source = foreign.append("assistant", "foreign workspace")
    store.index_path.unlink()
    for local in (store, full_fork, partial_fork):
        read = history.read(source, session_id=local.session_id)
        assert read is not None and read.content == "target evidence"
    with pytest.raises(ValueError, match="does not belong"):
        history.read(foreign_source, session_id=foreign.session_id)
    assert not store.index_path.exists()
    lines = partial_fork.current_path.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first.pop("project_path")
    lines[0] = json.dumps(first)
    partial_fork.current_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Cannot verify workspace"):
        history.read(source, session_id=partial_fork.session_id)
    local_alias = workspace / ".cade/sessions"
    local_alias.parent.mkdir(parents=True)
    local_alias.symlink_to(store.sessions_dir, target_is_directory=True)
    alias_history = SessionHistory(local_alias, project_root=workspace)
    with pytest.raises(ValueError, match="Cannot verify workspace"):
        alias_history.read(source, session_id=partial_fork.session_id)
    with pytest.raises(ValueError, match="does not belong"):
        alias_history.read(foreign_source, session_id=foreign.session_id)


def test_exact_artifact_pages_and_save_reject_missing_evidence(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / ".cade/sessions", project_root=tmp_path)
    content = "x" * 40000 + "ACTUAL_END"
    preview, reference = offload_large_tool_result(store.artifacts_dir, content)
    source = store.append(
        "event",
        {
            "type": "tool_result",
            "data": {"content": preview, "content_artifact": reference},
        },
    )
    session = store.session_id
    store.clear()
    store.append("assistant", "new session")
    history = _history(store)
    read = history.read(source, session_id=session, offset=40000, max_chars=1000)
    assert read is not None and "ACTUAL_END" in read.content
    assert read.next_offset is None
    assert reference is not None
    (store.artifacts_dir / f"{reference['artifact_id']}.txt").unlink()
    with pytest.raises(ValueError, match="missing or invalid"):
        history.read(source, session_id=session)
    save = build_save_memory_tool(tmp_path, history)
    with pytest.raises(ValueError, match="missing or invalid"):
        save.handler(
            {
                "path": ".cade/memory/bad.md",
                "markdown": "Not verified",
                "sources": [{"session_id": session, "entry_id": source}],
            },
            None,
        )
    assert not (tmp_path / ".cade/memory/bad.md").exists()


def test_memory_search_uses_existing_shell_without_expanding_tools(
    tmp_path: Path,
) -> None:
    if shutil.which("rg") is None:
        pytest.skip("Native shell search requires rg")
    directory = tmp_path / ".cade/memory"
    directory.mkdir(parents=True)
    (directory / "timeout.md").write_text("MEMORY_TIMEOUT", encoding="utf-8")
    (tmp_path / ".cade/private.md").write_text("PRIVATE_TIMEOUT", encoding="utf-8")
    (tmp_path / "visible.txt").write_text("VISIBLE_TIMEOUT", encoding="utf-8")
    (tmp_path / ".gitignore").write_text(".cade/\n", encoding="utf-8")
    shell = build_bash_tool(tmp_path)
    result = shell.handler({"command": 'rg "TIMEOUT" .cade/memory'}, None)
    assert "MEMORY_TIMEOUT" in result and "PRIVATE_TIMEOUT" not in result
    default = shell.handler({"command": 'rg "TIMEOUT" .'}, None)
    assert "VISIBLE_TIMEOUT" in default and "MEMORY_TIMEOUT" not in default
    grep = build_grep_tool(tmp_path)
    optional_default = grep.handler({"pattern": "TIMEOUT"}, None)
    assert (
        "VISIBLE_TIMEOUT" in optional_default
        and "MEMORY_TIMEOUT" not in optional_default
    )
    for mode in ("plan", "build", "act"):
        visible = policy_for_mode(mode).filter_tools((shell, grep))
        assert [tool.name for tool in visible] == ["bash"]
