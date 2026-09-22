"""Session 状态、结果和控制命令。"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from cade.harness.config import CadeRuntimeConfig, resolve_config_path

from .session_control import request_session_interrupt, session_run_status


def add_session_arguments(parser: argparse.ArgumentParser) -> None:
    """注册不依赖 provider 凭据的 session 子命令。"""
    parser.add_argument(
        "--project-root",
        type=Path,
        default=argparse.SUPPRESS,
        help="Project root directory.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=argparse.SUPPRESS,
        help="Runtime configuration file.",
    )
    parser.add_argument(
        "--sessions-dir",
        type=Path,
        default=argparse.SUPPRESS,
        help="Session transcript directory.",
    )
    actions = parser.add_subparsers(dest="session_action", required=True)
    for name in ("status", "tail", "result", "interrupt"):
        action = actions.add_parser(name)
        action.add_argument("session_id")
        action.add_argument("--json", action="store_true", dest="json_output")
        if name == "tail":
            action.add_argument("--lines", type=_positive_int, default=20)
    export = actions.add_parser("export")
    export.add_argument("session_id")
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--json", action="store_true", dest="json_output")


def handle_session_command(
    args: argparse.Namespace, runtime_config: CadeRuntimeConfig
) -> int:
    """执行 session 查询或控制操作。"""
    sessions_dir = (
        args.sessions_dir
        or resolve_config_path(args.project_root, runtime_config.paths.sessions_dir)
        or (args.project_root / ".cade" / "sessions")
    )
    try:
        path = _session_path(sessions_dir, args.session_id)
        action = args.session_action
        if action == "interrupt":
            accepted = request_session_interrupt(sessions_dir, args.session_id)
            payload = {
                "type": "session.interrupt",
                "session_id": args.session_id,
                "accepted": accepted,
            }
            _print_payload(payload, args.json_output)
            return 0 if accepted else 3

        if action == "tail":
            payload = {
                "type": "session.tail",
                "session_id": args.session_id,
                "entries": _tail_entries(path, args.lines),
            }
        elif action == "result":
            result = _last_result_from_path(path)
            if result is None:
                raise LookupError("session has no completed result")
            payload = {
                "type": "session.result",
                "session_id": args.session_id,
                "result": result,
            }
        elif action == "status":
            payload = _status_payload(
                sessions_dir,
                args.session_id,
                path,
                _iter_entries(path),
            )
        elif action == "export":
            entries = _read_entries(path)
            payload = _export_payload(sessions_dir, args.session_id, path, entries)
            _write_private_json(args.output, payload)
            payload = {
                "type": "session.exported",
                "session_id": args.session_id,
                "output": str(args.output),
            }
        else:
            raise ValueError(f"unsupported session action: {action}")
        _print_payload(payload, args.json_output)
        return 0
    except (LookupError, OSError, ValueError) as exc:
        payload = {
            "type": "session.error",
            "session_id": args.session_id,
            "error": {"type": type(exc).__name__, "message": str(exc)},
        }
        _print_payload(payload, getattr(args, "json_output", False))
        return 6 if isinstance(exc, ValueError) else 3


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _session_path(sessions_dir: Path, session_id: str) -> Path:
    if not session_id or any(
        char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
        for char in session_id
    ):
        raise ValueError("invalid session id")
    path = sessions_dir / f"session-{session_id}.jsonl"
    if not path.is_file():
        raise LookupError(f"session not found: {session_id}")
    return path


def _read_entries(path: Path) -> list[dict[str, Any]]:
    return list(_iter_entries(path))


def _iter_entries(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid session JSON at line {line_number}") from exc
            if isinstance(payload, dict):
                yield payload


def _tail_entries(path: Path, limit: int) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for entry in _iter_entries_reverse(path):
        entries.append(entry)
        if len(entries) >= limit:
            break
    entries.reverse()
    return entries


def _last_result_from_path(path: Path) -> dict[str, Any] | None:
    for entry in _iter_entries_reverse(path):
        result = _last_result([entry])
        if result is not None:
            return result
    return None


def _iter_entries_reverse(path: Path) -> Iterator[dict[str, Any]]:
    """从文件尾部按行解码，避免 tail/result 加载整个大 session。"""
    chunk_size = 64 * 1024
    with path.open("rb") as stream:
        stream.seek(0, os.SEEK_END)
        position = stream.tell()
        pending = b""
        while position > 0:
            read_size = min(chunk_size, position)
            position -= read_size
            stream.seek(position)
            pending = stream.read(read_size) + pending
            parts = pending.split(b"\n")
            pending = parts[0]
            for raw_line in reversed(parts[1:]):
                entry = _decode_entry_line(raw_line)
                if entry is not None:
                    yield entry
        entry = _decode_entry_line(pending)
        if entry is not None:
            yield entry


def _decode_entry_line(raw_line: bytes) -> dict[str, Any] | None:
    if not raw_line.strip():
        return None
    try:
        payload = json.loads(raw_line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid session JSON") from exc
    return payload if isinstance(payload, dict) else None


def _status_payload(
    sessions_dir: Path,
    session_id: str,
    path: Path,
    entries: Iterable[dict[str, Any]],
) -> dict[str, object]:
    counts: Counter[str] = Counter()
    entry_count = 0
    last_result: dict[str, Any] | None = None
    for entry in entries:
        entry_count += 1
        counts[str(entry.get("type", "unknown"))] += 1
        entry_result = _last_result([entry])
        if entry_result is not None:
            last_result = entry_result
    return {
        "type": "session.status",
        "session_id": session_id,
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "updated_at": datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(),
        "entry_count": entry_count,
        "entry_types": dict(sorted(counts.items())),
        "run": session_run_status(sessions_dir, session_id),
        "has_result": last_result is not None,
        "result_status": (
            last_result.get("status") or last_result.get("termination_reason")
            if isinstance(last_result, dict)
            else None
        ),
    }


def _last_result(entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    for entry in reversed(entries):
        if entry.get("type") != "event":
            continue
        content = entry.get("content")
        if not isinstance(content, dict):
            continue
        event_type = content.get("type")
        data = content.get("data")
        if event_type in {"exec_result", "final"} and isinstance(data, dict):
            return data
    return None


def _export_payload(
    sessions_dir: Path,
    session_id: str,
    path: Path,
    entries: list[dict[str, Any]],
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "session": _status_payload(sessions_dir, session_id, path, entries),
        "result": _last_result(entries),
        "entries": entries,
    }


def _print_payload(payload: dict[str, object], json_output: bool) -> None:
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        return
    event_type = payload.get("type", "session")
    print(f"[{event_type}] {json.dumps(payload, ensure_ascii=False, indent=2)}")


def _write_private_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
