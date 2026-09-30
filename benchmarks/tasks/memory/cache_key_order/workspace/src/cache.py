"""按请求参数索引的进程内结果缓存。"""

from __future__ import annotations

from collections.abc import Mapping

_STORE: dict[str, str] = {}


def cache_key(parameters: Mapping[str, str]) -> str:
    """把请求参数拼成缓存键。"""
    return "&".join(f"{name}={value}" for name, value in parameters.items())


def get(parameters: Mapping[str, str]) -> str | None:
    """读取缓存值，未命中时返回 None。"""
    return _STORE.get(cache_key(parameters))


def put(parameters: Mapping[str, str], value: str) -> None:
    """写入缓存值。"""
    _STORE[cache_key(parameters)] = value
