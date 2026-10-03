"""exec 进程与 session CLI 之间的跨进程控制面。"""

from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


class SessionRunControl:
    """通过 session 目录中的原子标记协作中断活动 run。"""

    def __init__(self, sessions_dir: Path, session_id: str, app: Any) -> None:
        self._paths = _control_paths(sessions_dir, session_id)
        self._session_id = session_id
        self._app = app
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._paths.control_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._paths.interrupt.unlink(missing_ok=True)
        _write_private_json(
            self._paths.active,
            {
                "session_id": self._session_id,
                "pid": os.getpid(),
                "started_at": datetime.now(UTC).isoformat(),
            },
        )
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.3)
        self._paths.active.unlink(missing_ok=True)
        self._paths.interrupt.unlink(missing_ok=True)

    def _watch(self) -> None:
        while not self._stop.wait(0.1):
            if not self._paths.interrupt.exists():
                continue
            if self._app.agent.interrupt("interrupted by session command"):
                return


def session_run_status(sessions_dir: Path, session_id: str) -> dict[str, object]:
    """返回活动 run 投影，并清理明显过期的标记。"""
    paths = _control_paths(sessions_dir, session_id)
    payload = _read_json(paths.active)
    if payload is None:
        paths.active.unlink(missing_ok=True)
        paths.interrupt.unlink(missing_ok=True)
        return {"active": False}
    pid = payload.get("pid")
    if not isinstance(pid, int) or not _process_exists(pid):
        paths.active.unlink(missing_ok=True)
        paths.interrupt.unlink(missing_ok=True)
        return {"active": False}
    return {
        "active": True,
        "pid": pid,
        "started_at": payload.get("started_at"),
        "interrupt_requested": paths.interrupt.exists(),
    }


def request_session_interrupt(sessions_dir: Path, session_id: str) -> bool:
    """仅向活动 Cade run 写入中断请求。"""
    status = session_run_status(sessions_dir, session_id)
    if not status["active"]:
        return False
    paths = _control_paths(sessions_dir, session_id)
    _write_private_json(
        paths.interrupt,
        {
            "session_id": session_id,
            "requested_at": datetime.now(UTC).isoformat(),
        },
    )
    return True


class _ControlPaths:
    def __init__(self, sessions_dir: Path, session_id: str) -> None:
        self.control_dir = sessions_dir / ".control"
        self.active = self.control_dir / f"{session_id}.active.json"
        self.interrupt = self.control_dir / f"{session_id}.interrupt.json"


def _control_paths(sessions_dir: Path, session_id: str) -> _ControlPaths:
    if not session_id or any(
        char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
        for char in session_id
    ):
        raise ValueError("invalid session id")
    return _ControlPaths(sessions_dir, session_id)


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _read_json(path: Path) -> dict[str, object] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_private_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
