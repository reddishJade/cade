from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

T = TypeVar("T")

_file_locks: dict[Path, tuple[threading.Lock, int]] = {}
_registry_lock = threading.Lock()


def _resolve_key(file_path: Path) -> Path:
    try:
        return file_path.resolve()
    except (OSError, ValueError):
        return file_path.absolute()


def with_file_mutation[T](file_path: Path, fn: Callable[[], T]) -> T:
    key = _resolve_key(file_path)
    with _registry_lock:
        entry = _file_locks.get(key)
        mutex, waiting = entry if entry is not None else (threading.Lock(), 0)
        _file_locks[key] = (mutex, waiting + 1)
    try:
        with mutex:
            return fn()
    finally:
        with _registry_lock:
            current_lock, remaining = _file_locks[key]
            if remaining == 1:
                del _file_locks[key]
            else:
                _file_locks[key] = (current_lock, remaining - 1)
