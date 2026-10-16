"""保护正常编码及词表加载失败时的字节估算契约。

运行：uv run --locked pytest -q src/cade/tests/test_token_estimation_contract.py。
正常路径使用本地单字节词表，预期值按 UTF-8 字节数独立核对。
故障路径在词表加载边界注入真实 requests 网络异常及文件 I/O 异常。
空串、短串、英文和中文分别验证最小计数、除法取整及多字节处理。
遗漏 OSError 捕获会使故障用例抛异常；忽略真实编码会使正常用例计数错误。
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest
import tiktoken
from requests.exceptions import ConnectionError, ProxyError, SSLError, Timeout

from cade.agent._context_window import estimate_tokens


@pytest.mark.parametrize(
    ("text", "expected"),
    [("", 1), ("ab", 2), ("abcdef", 6), ("中文测试", 12)],
)
def test_estimate_tokens_uses_encoding(text: str, expected: int) -> None:
    assert estimate_tokens(text) == expected


@pytest.mark.parametrize(
    "error_type",
    [ProxyError, ConnectionError, Timeout, SSLError, OSError, PermissionError],
)
@pytest.mark.parametrize(
    ("text", "expected"),
    [("", 1), ("ab", 1), ("abcdef", 2), ("中文测试", 4)],
)
def test_estimate_tokens_falls_back_when_vocabulary_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[OSError],
    text: str,
    expected: int,
) -> None:
    monkeypatch.setattr(
        tiktoken, "get_encoding", Mock(side_effect=error_type("Vocabulary unavailable"))
    )
    assert estimate_tokens(text) == expected
