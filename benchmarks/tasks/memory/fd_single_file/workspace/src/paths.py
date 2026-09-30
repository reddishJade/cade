"""路径归一化工具。"""

from __future__ import annotations

import os


def to_repo_path(path: str) -> str:
    """把本地路径规范为以 / 分隔的相对路径。"""
    text = path.replace(os.sep, "/")
    while text.startswith("./"):
        text = text[2:]
    return text


def is_python_source(path: str) -> bool:
    """判断路径是否为 Python 源文件。"""
    return to_repo_path(path).endswith(".py")
