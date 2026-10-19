"""验证增量读取不重复解析审计，并保持分支、提交和精确读取语义。

运行本模块。用解码次数与真实文件读取断言工作量，再用原始日志独立验证
投影结果；覆盖外部追加、失败提交、文件替换和旧 head，防止缓存成为权威。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from cade.harness.session import store as storage
from cade.harness.session.history import SessionHistory
from cade.harness.session.recorder import SessionRecorder
from cade.harness.session.runtime import RuntimeSession
from cade.harness.session.store import SessionStore, read_session_entries
from cade.harness.session.surface import project_session_surface
from cade.harness.session.types import SessionEntry


def audit(store: SessionStore) -> str:
    return store.append(
        "event",
        {
            "schema_version": 3,
            "type": "provider_request",
            "data": {
                "messages": [{"role": "user", "content": "audit-body" * 1000}],
                "tools": [{"name": "example", "parameters": {}}],
                "request_digest": "original",
            },
        },
    )


def test_warm_reads_decode_only_new_records_and_keep_exact_audits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SessionStore(tmp_path / "sessions")
    first = store.append("assistant", "First")
    request = audit(store)
    decoded: list[int] = []
    decode = storage._decode_entry

    def counted(payload: bytes) -> SessionEntry:
        decoded.append(len(payload))
        return decode(payload)

    monkeypatch.setattr(storage, "_decode_entry", counted)
    view = store.read_view()
    assert len(decoded) == 2
    assert view.branch_ids == (first, request)
    assert [entry.id for entry in view.facts()] == [first]
    decoded.clear()
    opened: list[MagicMock] = []
    original_open = Path.open

    def tracked_open(path: Path, *args, **kwargs):
        stream = original_open(path, *args, **kwargs)
        if path == store.current_path and args == ("rb",):
            tracked = MagicMock(wraps=stream)
            tracked.__enter__.return_value = tracked
            tracked.__exit__.side_effect = lambda *unused: stream.close()
            opened.append(tracked)
            return tracked
        return stream

    monkeypatch.setattr(Path, "open", tracked_open)
    assert store.read_view().facts() == view.facts()
    assert not decoded
    assert opened
    assert all(
        not stream.read.called and not stream.readline.called for stream in opened
    )
    monkeypatch.setattr(Path, "open", original_open)
    second = store.append("assistant", "Second")
    updated = store.read_view()
    assert len(decoded) == 1
    assert updated.branch_ids == (first, request, second)
    assert view.branch_ids == (first, request)
    assert updated.get(request) == read_session_entries(store.current_path)[1]
    assert view.get(request) == updated.get(request)


def test_views_and_exact_records_do_not_share_mutable_content(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions")
    entry_id = store.append("event", {"type": "example", "data": {"value": "original"}})
    request = audit(store)
    view = store.read_view()
    facts = view.facts()
    facts[0].content["data"]["value"] = "changed"
    exact = view.get(request)
    assert exact is not None
    exact.content["data"]["messages"].clear()
    assert store.read_view().facts()[0].content["data"]["value"] == "original"
    assert view.get(entry_id).content["data"]["value"] == "original"
    assert view.get(request).content["data"]["messages"]


def test_index_follows_rewind_fork_and_session_switch(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions")
    first = store.append("assistant", "First")
    request = audit(store)
    second = store.append("assistant", "Second")
    old = store.read_view()
    assert store.jump_to_entry(first)
    branch = store.append("assistant", "Alternative")
    assert store.read_view().branch_ids == (first, branch)
    assert store.read_view().get(second) is not None
    assert old.branch_ids == (first, request, second)
    fork = store.fork_into()
    fork.append("assistant", "Fork")
    assert fork.read_view().branch() == fork.build_branch()
    assert store.read_view().branch_ids == (first, branch)
    original_path = store.current_path
    store.clear()
    assert store.read_view().branch_ids == ()
    store.resume(original_path)
    assert store.read_view().branch_ids == (first, branch)
    assert store.read_entries() == read_session_entries(original_path)


def test_legacy_head_changes_do_not_require_file_changes(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions")
    records = [
        {
            "id": "first",
            "parent_id": None,
            "type": "assistant",
            "content": "First",
            "created_at": "old",
        },
        {
            "id": "second",
            "parent_id": "first",
            "type": "assistant",
            "content": "Second",
            "created_at": "old",
        },
    ]
    store.current_path.write_text(
        "".join(json.dumps(record) + "\n" for record in records)
    )
    store.ensure_metadata()
    store._save_head_id("first")
    old = store.read_view()
    assert old.branch_ids == ("first",)
    store._save_head_id("second")
    new = store.read_view()
    assert new.branch_ids == ("first", "second")
    assert new.revision.committed_bytes == old.revision.committed_bytes
    assert new.revision != old.revision


def test_partial_tail_and_failure_after_commit_reconcile_from_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SessionStore(tmp_path / "sessions")
    first = store.append("assistant", "First")
    store.read_view()
    with store.current_path.open("ab") as stream:
        stream.write(b'{"id":"torn')
    assert store.read_view().branch_ids == (first,)
    second = store.append("assistant", "After repair")
    assert store.read_view().branch_ids == (first, second)

    def fail(entry_id: str) -> None:
        raise OSError("after durable append")

    monkeypatch.setattr(store, "_save_head_id", fail)
    with pytest.raises(OSError, match="after durable append"):
        store.append("assistant", "Committed")
    assert store.read_view().branch() == read_session_entries(store.current_path)
    assert store.read_view().facts()[-1].content == "Committed"


def test_replacement_truncation_and_exact_read_revision(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions")
    store.append("assistant", "First")
    prefix = store.current_path.read_bytes()
    request = audit(store)
    old = store.read_view()
    payload = store.current_path.read_bytes()
    replacement = store.current_path.with_suffix(".replacement")
    replacement.write_bytes(payload)
    os.replace(replacement, store.current_path)
    assert store.read_view().branch() == read_session_entries(store.current_path)
    with pytest.raises(ValueError, match="changed"):
        old.get(request)
    before = store.read_view()
    store.current_path.write_bytes(payload.replace(b"original", b"modified"))
    with pytest.raises(ValueError, match="changed"):
        before.get(request)
    assert (
        store.read_view().get(request).content["data"]["request_digest"] == "modified"
    )
    store.current_path.write_bytes(prefix)
    assert len(store.read_view().branch_ids) == 1


@pytest.mark.parametrize("bad", ["invalid", "duplicate", "parent", "head"])
def test_committed_corruption_is_rejected_even_after_warm_read(
    tmp_path: Path, bad: str
) -> None:
    store = SessionStore(tmp_path / "sessions")
    entry_id = store.append("assistant", "First")
    store.read_view()
    entry = {
        "id": entry_id if bad == "duplicate" else "other",
        "parent_id": "missing" if bad == "parent" else entry_id,
        "head_id": "missing" if bad == "head" else "other",
        "type": "assistant",
        "content": "Bad",
        "created_at": "now",
    }
    with store.current_path.open("a") as stream:
        stream.write("invalid\n" if bad == "invalid" else json.dumps(entry) + "\n")
    for _ in range(2):
        with pytest.raises(ValueError):
            store.read_view()


def test_external_append_is_visible_and_shares_the_commit_lock(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions")
    store.append("assistant", "First")
    before = store.read_view()
    program = (
        "import sys;from pathlib import Path;"
        "from cade.harness.session.store import SessionStore;"
        "print('ready',flush=True);"
        "s=SessionStore(Path(sys.argv[1]));s.current_path=Path(sys.argv[2]);"
        "s.append('assistant','External')"
    )
    with store._lock:
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                program,
                str(store.sessions_dir),
                str(store.current_path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "ready"
        assert process.poll() is None
        assert store.read_view().revision == before.revision
    stdout, stderr = process.communicate(timeout=10)
    assert process.returncode == 0, stdout + stderr
    assert store.read_view().facts()[-1].content == "External"
    assert len(before.branch_ids) == 1


def test_rollover_lineage_keeps_audit_ids_without_loading_audit_bodies(
    tmp_path: Path,
) -> None:
    store = SessionStore(tmp_path / "sessions")
    first = store.append("assistant", "Before")
    request = audit(store)
    recorder = SessionRecorder(store)
    recorder.record_context_window_reset(
        window_id="window", messages_before=1, messages_after=0, replacement=[]
    )
    view = store.read_view()
    reset = view.facts("context_window_reset")[0]
    assert reset.content["data"]["source_entry_ids"] == [first, request]
    assert request not in [entry.id for entry in view.facts()]
    store.append("assistant", "After")
    expected = project_session_surface(store.build_branch()).messages
    assert tuple(RuntimeSession(recorder).history_messages()) == expected
    assert len(expected) == 1


def test_failed_append_does_not_publish_a_new_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SessionStore(tmp_path / "sessions")
    store.append("assistant", "Before")
    before = store.read_view()

    def fail(entry) -> None:
        raise OSError("before append")

    monkeypatch.setattr(store, "_append_entry", fail)
    with pytest.raises(OSError, match="before append"):
        store.append("assistant", "Not committed")
    assert store.read_view().revision == before.revision
    assert store.read_view().facts() == before.facts()


def test_valid_legacy_record_without_newline_retains_existing_read_semantics(
    tmp_path: Path,
) -> None:
    store = SessionStore(tmp_path / "sessions")
    store.append("assistant", "Before")
    store.current_path.write_bytes(store.current_path.read_bytes().rstrip(b"\n"))
    assert store.read_view().branch() == read_session_entries(store.current_path)
    store.append("assistant", "After")
    assert store.read_view().branch() == read_session_entries(store.current_path)


def test_history_uses_shared_view_but_retains_exact_audit_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SessionStore(tmp_path / "sessions", project_root=tmp_path)
    store.append("assistant", "Unique primary evidence")
    request = audit(store)
    history = SessionHistory(store.sessions_dir, project_root=tmp_path, store=store)
    history.set_session_id(store.session_id)
    store.read_view()

    def full_scan(*args, **kwargs):
        raise AssertionError("History performed an independent full scan")

    monkeypatch.setattr("cade.harness.session.history.read_session_entries", full_scan)
    assert len(history.search("Unique")) == 1
    exact = history.read(request)
    assert exact is not None and "audit-body" in exact.content
    assert history.get_entry(store.session_id, request) == store.read_view().get(
        request
    )
    assert [entry.id for entry in history.around(request, before=1)] == list(
        store.read_view().branch_ids
    )
