"""输出滚动缓冲区与截断。

OpenCode 风格的滚动缓冲区设计：
- keep = maxBytes * 2，保留截断余量
- 溢出后才写文件（惰性写入），小输出纯内存
- 按输出块推送进度
"""

from __future__ import annotations

import logging
import os
import tempfile
from collections.abc import Callable
from typing import BinaryIO, cast

from .truncate import DEFAULT_MAX_BYTES, DEFAULT_MAX_LINES, truncate_tail

logger = logging.getLogger("cade.coding_agent.tools.output_accumulator")


class _ChunkEntry:
    """滚动缓冲区中的单个条目。"""

    __slots__ = ("size", "text")

    def __init__(self, text: str, size: int) -> None:
        self.text = text
        self.size = size


class PersistedOutputFile:
    def __init__(self, temp_prefix: str) -> None:
        fd, path = tempfile.mkstemp(suffix=".log", prefix=temp_prefix)
        self._file = cast(
            BinaryIO,
            os.fdopen(fd, "w+b"),
        )
        self.path = path

    def append(self, chunk: bytes) -> None:
        self._file.write(chunk)

    def close(self) -> None:
        self._file.flush()
        self._file.close()


class OutputAccumulator:
    """滚动缓冲区输出累加器。

    设计遵循 OpenCode：
    - keep = max_bytes * 2：滚动窗口保留 2x 上限，给截断留余量
    - 溢出后惰性写文件（超过字节或行数上限时落盘）
    - 按输出块推送进度
    """

    def __init__(
        self,
        max_bytes: int = DEFAULT_MAX_BYTES,
        max_lines: int = DEFAULT_MAX_LINES,
        temp_prefix: str = "cade-bash-",
        on_progress: Callable[[str], None] | None = None,
    ) -> None:
        self._max_bytes = max_bytes
        self._max_lines = max_lines
        self._temp_prefix = temp_prefix
        self._on_progress = on_progress

        # 滚动窗口（保留 max_bytes * 2 字节）
        self._keep = max_bytes * 2
        self._list: list[_ChunkEntry] = []
        self._used = 0  # 当前窗口总字节数

        # 完整累计统计（不因滚动而丢失）
        self._total_lines = 0
        self._total_bytes = 0

        # 惰性文件 spill
        self._persisted_output: PersistedOutputFile | None = None
        self._finished = False

    # ── 属性 ──

    @property
    def total_bytes(self) -> int:
        return self._total_bytes

    # ── 追加数据 ──

    def append(self, chunk: bytes) -> None:
        if self._finished:
            raise RuntimeError("cannot append after close")
        text = chunk.decode("utf-8", errors="replace")
        size = len(chunk)

        self._total_bytes += size
        self._total_lines += text.count("\n")

        # 追加到滚动窗口
        self._list.append(_ChunkEntry(text, size))
        self._used += size

        # 滚动：超出 keep 时从头部移除
        while self._used > self._keep and len(self._list) > 1:
            entry = self._list.pop(0)
            self._used -= entry.size

        # 如果已 spill 到文件，同步写入
        if self._persisted_output is not None:
            self._persisted_output.append(chunk)

        # 首次超限 → 惰性 spill
        if self._persisted_output is None and (
            self._total_bytes > self._max_bytes or self._total_lines > self._max_lines
        ):
            self._spill()

        # 进度回调
        if self._on_progress:
            self._on_progress(text)

    def _spill(self) -> None:
        """将完整内容写入文件，内存仅保留尾部窗口。"""
        if self._persisted_output is not None:
            return
        persisted = PersistedOutputFile(self._temp_prefix)
        for entry in self._list:
            persisted.append(entry.text.encode("utf-8", errors="replace"))
        self._persisted_output = persisted

    # ── 快照 ──

    def snapshot(self) -> str:
        # 拼接滚动窗口内容
        text = "".join(e.text for e in self._list)
        if not text and self._persisted_output is None:
            return "(no output)"

        tr = truncate_tail(text, max_lines=self._max_lines, max_bytes=self._max_bytes)
        if self._persisted_output is not None:
            footer = (
                f"\n[Showing {tr.output_lines} of {self._total_lines} lines"
                f" ({self._max_bytes // 1024}KB limit)."
                f" Full output: {self._persisted_output.path}]"
            )
            return tr.content + footer
        return tr.content

    # ── 清理 ──

    def close(self) -> None:
        self._finished = True
        if self._persisted_output is not None:
            try:
                self._persisted_output.close()
            except Exception:
                logger.debug(
                    "failed to close temp file %s",
                    self._persisted_output.path,
                    exc_info=True,
                )
