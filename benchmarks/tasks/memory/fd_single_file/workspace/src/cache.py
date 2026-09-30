"""发现结果的进程内缓存。"""

from __future__ import annotations

_CACHE: dict[tuple[str, str], list[str]] = {}


def remember(root: str, suffix: str, paths: list[str]) -> None:
    """缓存一次发现结果。"""
    _CACHE[(root, suffix)] = list(paths)


def lookup(root: str, suffix: str) -> list[str] | None:
    """返回缓存的发现结果，未命中时返回 None。"""
    cached = _CACHE.get((root, suffix))
    return list(cached) if cached is not None else None
