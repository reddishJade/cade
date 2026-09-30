"""真实 JSONL、索引和子进程退出的恢复回归。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from cade.harness.session import SessionHistory, SessionStore


def _reopen(store: SessionStore) -> SessionStore:
    reopened = SessionStore(store.sessions_dir, project_root=store.project_root)
    reopened.resume(store.current_path)
    return reopened


def _history(store: SessionStore) -> SessionHistory:
    history = SessionHistory(
        store.sessions_dir,
        project_root=store.project_root,
        artifacts_dir=store.artifacts_dir,
    )
    history.set_session_id(store.session_id)
    return history


def _report(store: SessionStore, tmp_path: Path) -> None:
    (tmp_path / "recovery-report.json").write_text(
        json.dumps(
            {
                "branch": [entry.id for entry in store.build_branch()],
                "records": len(store.read_entries()),
                "path": str(store.current_path),
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def test_log_commit_survives_process_exit_before_index_update(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions", project_root=tmp_path)
    store.ensure_metadata("crash at index boundary")
    store.append("assistant", "before crash")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import os
import sys
from pathlib import Path
from cade.harness.session.tree_store import TreeSessionRepo
class CrashRepo(TreeSessionRepo):
    def _save_head_id(self, entry_id: str) -> None:
        os._exit(77)
repo = CrashRepo(Path(sys.argv[1]))
repo.resume(Path(sys.argv[2]))
repo.append("assistant", "durable after crash")
""",
            str(store.sessions_dir),
            str(store.current_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    (tmp_path / "crash-process.json").write_text(
        json.dumps(
            {
                "exit_code": result.returncode,
                "stderr": result.stderr,
            }
        ),
        encoding="utf-8",
    )
    assert result.returncode == 77
    reopened = _reopen(store)
    assert [entry.content for entry in reopened.build_branch()] == [
        "before crash",
        "durable after crash",
    ]
    assert len(_history(reopened).search("durable")) == 1
    store.index_path.unlink()
    exact = _history(reopened).read(
        reopened.build_branch()[-1].id, session_id=store.session_id
    )
    assert exact is not None and exact.content == "durable after crash"
    _report(reopened, tmp_path)


def test_missing_index_restores_full_branch_and_durable_rewind(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions", project_root=tmp_path)
    store.ensure_metadata("long branch")
    first = store.append("assistant", "root evidence")
    for index in range(205):
        store.append("assistant", f"discarded branch {index}")
    store.index_path.unlink()
    reopened = _reopen(store)
    assert len(reopened.build_branch()) == 206
    assert reopened.jump_to_entry(first)
    reopened.index_path.unlink(missing_ok=True)
    after_rewind = _reopen(reopened)
    assert [entry.id for entry in after_rewind.build_branch()] == [first]
    assert _history(after_rewind).search("discarded") == []
    new = after_rewind.append("assistant", "new branch")
    assert [(entry.id, entry.parent_id) for entry in after_rewind.build_branch()] == [
        (first, None),
        (new, first),
    ]
    _report(after_rewind, tmp_path)


@pytest.mark.parametrize("tail", [b'{"id":"partial', b'{"content":"\xe4\xb8', b""])
def test_torn_tail_can_be_recovered_then_appended(tmp_path: Path, tail: bytes) -> None:
    store = SessionStore(tmp_path / "sessions", project_root=tmp_path)
    store.ensure_metadata("torn tail")
    first = store.append("assistant", "complete evidence")
    if tail:
        with store.current_path.open("ab") as stream:
            stream.write(tail)
    else:
        store.current_path.write_bytes(store.current_path.read_bytes().rstrip(b"\n"))
    reopened = _reopen(store)
    assert [entry.id for entry in reopened.build_branch()] == [first]
    second = reopened.append("assistant", "continued after recovery")
    assert [entry.id for entry in reopened.build_branch()] == [first, second]
    assert len(_history(reopened).search("continued after recovery")) == 1
    for line in reopened.current_path.read_text(encoding="utf-8").splitlines():
        json.loads(line)
    _report(reopened, tmp_path)


@pytest.mark.parametrize(
    "damage", ["middle_json", "missing_parent", "cycle", "duplicate_id"]
)
def test_corrupt_committed_records_are_rejected(tmp_path: Path, damage: str) -> None:
    store = SessionStore(tmp_path / "sessions", project_root=tmp_path)
    store.ensure_metadata("corruption")
    store.append("assistant", "first")
    store.append("assistant", "second")
    lines = store.current_path.read_text(encoding="utf-8").splitlines()
    second = json.loads(lines[1])
    if damage == "middle_json":
        lines.insert(1, "invalid complete record")
    elif damage == "missing_parent":
        second["parent_id"] = "missing"
    elif damage == "cycle":
        second["parent_id"] = second["id"]
    else:
        second["id"] = json.loads(lines[0])["id"]
    if damage != "middle_json":
        lines[1] = json.dumps(second)
    store.current_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        _reopen(store).build_branch()
    with pytest.raises(ValueError):
        _history(store).search("first")


def test_process_exit_before_index_replace_preserves_previous_index(
    tmp_path: Path,
) -> None:
    store = SessionStore(tmp_path / "sessions", project_root=tmp_path)
    store.ensure_metadata("keep the existing index")
    store.append("assistant", "before replacement")
    original_index = store.index_path.read_bytes()
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import os
import sys
from pathlib import Path
from cade.harness.session import SessionStore
repo = SessionStore(Path(sys.argv[1]))
repo.resume(Path(sys.argv[2]))
def crash_at_replace(source, destination):
    os._exit(78)
os.replace = crash_at_replace
repo.append("assistant", "committed before replacement")
""",
            str(store.sessions_dir),
            str(store.current_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 78
    assert store.index_path.read_bytes() == original_index
    assert (
        json.loads(original_index)["sessions"][0]["title"] == "keep the existing index"
    )
    recovered = _reopen(store)
    assert recovered.build_branch()[-1].content == "committed before replacement"
    recovered.append("assistant", "continued")
    assert recovered.current_metadata().head_id == recovered.build_branch()[-1].id
    _report(recovered, tmp_path)
