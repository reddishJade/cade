"""为回归测试提供本地词表，隔离网络和机器缓存。

仅替换词表加载边界，保留真实 tiktoken 编码和特殊标记检查。
词表包含全部 256 个单字节 token，不含合并规则，故计数等于 UTF-8 字节数。
这些测试保护预算策略和异常恢复，不校准实际模型的 token 用量。
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest
import tiktoken


@pytest.fixture(autouse=True)
def local_token_encoding(monkeypatch: pytest.MonkeyPatch) -> tiktoken.Encoding:
    encoding = tiktoken.Encoding(
        name="test_bytes",
        pat_str=r"(?s).",
        mergeable_ranks={bytes([value]): value for value in range(256)},
        special_tokens={
            "<|endoftext|>": 256,
            "<|fim_prefix|>": 257,
            "<|fim_middle|>": 258,
            "<|fim_suffix|>": 259,
            "<|endofprompt|>": 260,
        },
    )
    monkeypatch.setattr(tiktoken, "get_encoding", Mock(return_value=encoding))
    return encoding
