"""按目录遍历发现 Python 源文件。"""

from __future__ import annotations

import os
from collections.abc import Iterator

from src.paths import to_repo_path

SUFFIX = ".py"


class DiscoveryError(ValueError):
    """发现流程无法处理给定输入。"""


def iter_sources(root: str) -> Iterator[str]:
    """遍历 root 下的匹配文件，返回以 / 分隔的路径。"""
    if not os.path.isdir(root):
        raise DiscoveryError(f"discovery target is not a directory: {root}")
    for current, _directories, files in os.walk(root):
        for name in sorted(files):
            if name.endswith(SUFFIX):
                yield to_repo_path(os.path.join(current, name))


def discover(root: str) -> list[str]:
    """返回 root 下全部 Python 源文件。"""
    return list(iter_sources(root))
