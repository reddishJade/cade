"""Session 查询、导出与中断命令测试。"""

from __future__ import annotations

import argparse
import json
import threading
from pathlib import Path
from types import SimpleNamespace

from cade.cli.session_cmd import handle_session_command
from cade.cli.session_control import SessionRunControl
from cade.harness.config import CadeRuntimeConfig
from cade.main import parse_args


def _write_session(sessions_dir: Path, session_id: str) -> Path:
    sessions_dir.mkdir(parents=True)
    path = sessions_dir / f"session-{session_id}.jsonl"
    entries = [
        {
            "id": "one",
            "parent_id": None,
            "type": "user",
            "content": {"text": "fix it"},
            "created_at": "2026-09-23T00:00:00+00:00",
        },
        {
            "id": "two",
            "parent_id": "one",
            "type": "event",
            "content": {
                "schema_version": 1,
                "type": "final",
                "step": 1,
                "data": {"answer": "old", "termination_reason": "completed"},
                "correlation": {},
            },
            "created_at": "2026-09-23T00:00:01+00:00",
        },
        {
            "id": "three",
            "parent_id": "two",
            "type": "event",
            "content": {
                "schema_version": 1,
                "type": "exec_result",
                "step": 1,
                "data": {
                    "status": "completed",
                    "exit_code": 0,
                    "answer": "done",
                },
                "correlation": {},
            },
            "created_at": "2026-09-23T00:00:02+00:00",
        },
    ]
    path.write_text(
        "".join(json.dumps(entry) + "\n" for entry in entries),
        encoding="utf-8",
    )
    return path


def _args(tmp_path: Path, action: str, *extra: str) -> argparse.Namespace:
    return parse_args(
        [
            "session",
            "--project-root",
            str(tmp_path),
            "--sessions-dir",
            str(tmp_path / "sessions"),
            action,
            "run-1",
            *extra,
        ]
    )


def test_session_status_and_result_are_machine_readable(tmp_path: Path, capsys) -> None:
    sessions_dir = tmp_path / "sessions"
    _write_session(sessions_dir, "run-1")

    status = handle_session_command(
        _args(tmp_path, "status", "--json"), CadeRuntimeConfig()
    )
    status_payload = json.loads(capsys.readouterr().out)
    result = handle_session_command(
        _args(tmp_path, "result", "--json"), CadeRuntimeConfig()
    )
    result_payload = json.loads(capsys.readouterr().out)

    assert status == 0
    assert status_payload["entry_count"] == 3
    assert status_payload["has_result"] is True
    assert status_payload["result_status"] == "completed"
    assert status_payload["run"] == {"active": False}
    assert result == 0
    assert result_payload["result"]["answer"] == "done"


def test_session_tail_and_export(tmp_path: Path, capsys) -> None:
    sessions_dir = tmp_path / "sessions"
    _write_session(sessions_dir, "run-1")

    assert (
        handle_session_command(
            _args(tmp_path, "tail", "--lines", "1", "--json"),
            CadeRuntimeConfig(),
        )
        == 0
    )
    tail_payload = json.loads(capsys.readouterr().out)
    output = tmp_path / "run.json"
    assert (
        handle_session_command(
            _args(tmp_path, "export", "--output", str(output), "--json"),
            CadeRuntimeConfig(),
        )
        == 0
    )
    export_payload = json.loads(capsys.readouterr().out)

    assert len(tail_payload["entries"]) == 1
    assert tail_payload["entries"][0]["id"] == "three"
    assert export_payload["output"] == str(output)
    exported = json.loads(output.read_text(encoding="utf-8"))
    assert output.stat().st_mode & 0o777 == 0o600
    assert exported["result"]["answer"] == "done"
    assert len(exported["entries"]) == 3


def test_session_interrupt_reaches_active_exec(tmp_path: Path, capsys) -> None:
    sessions_dir = tmp_path / "sessions"
    _write_session(sessions_dir, "run-1")
    interrupted = threading.Event()
    app = SimpleNamespace(
        agent=SimpleNamespace(
            interrupt=lambda _reason: interrupted.set() or True,
        )
    )
    control = SessionRunControl(sessions_dir, "run-1", app)
    control.start()
    try:
        status = handle_session_command(
            _args(tmp_path, "interrupt", "--json"), CadeRuntimeConfig()
        )
        payload = json.loads(capsys.readouterr().out)
        assert interrupted.wait(timeout=1)
    finally:
        control.stop()

    assert status == 0
    assert payload["accepted"] is True
