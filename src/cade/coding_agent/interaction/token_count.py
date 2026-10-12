from __future__ import annotations

import re

_TOKEN_COUNT = re.compile(r"(\d+(?:\.\d+)?)([km]?)")
_UNITS = {"": 1, "k": 1_000, "m": 1_000_000}


def parse_token_count(text: str) -> int:
    """解析 `200000`、`200k`、`1.5m` 形式的 token 数量；非法或非正数抛 ValueError。"""
    match = _TOKEN_COUNT.fullmatch(text.strip().lower())
    if match is None:
        raise ValueError(f"Expected a token count like 200k or 1m, got: {text!r}")
    count = round(float(match.group(1)) * _UNITS[match.group(2)])
    if count <= 0:
        raise ValueError("Token count must be positive.")
    return count


def format_token_count(count: int) -> str:
    """把 token 数量显示为 `200k`、`1.05m` 这类紧凑形式，保持可被解析器读回。"""
    for unit, size in (("m", 1_000_000), ("k", 1_000)):
        if count >= size:
            return f"{count / size:.2f}".rstrip("0").rstrip(".") + unit
    return str(count)
