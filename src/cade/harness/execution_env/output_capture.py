"""在执行期间保存完整输出，并以有界内存保留输出尾部。"""

from __future__ import annotations

import codecs
import tempfile
from contextlib import ExitStack
from typing import BinaryIO, cast


class OutputCapture:
    """输出超过上限时写入临时文件，内存只保留有限尾部。"""

    def __init__(self, max_bytes: int = 50 * 1024) -> None:
        self._max_bytes = max_bytes
        self._data = bytearray()
        self._stream: BinaryIO | None = None
        self._stack = ExitStack()
        self._path: str | None = None
        self._newlines = 0
        self._last_byte: int | None = None
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")

    def append(self, data: bytes) -> str:
        if not data:
            return ""
        self._newlines += data.count(b"\n")
        self._last_byte = data[-1]
        if self._stream is None and len(self._data) + len(data) > self._max_bytes:
            stream = self._stack.enter_context(
                tempfile.NamedTemporaryFile(  # noqa: SIM115
                    mode="wb", prefix="cade-bash-", suffix=".log", delete=False
                )
            )
            self._path = stream.name
            stream.write(self._data)
            self._stream = cast(BinaryIO, stream)

        if self._stream is not None:
            self._stream.write(data)
        self._data.extend(data)
        if len(self._data) > self._max_bytes:
            del self._data[: -self._max_bytes]
        return self._decoder.decode(data, final=False)

    def finish(self) -> tuple[str, str | None, int]:
        self._decoder.decode(b"", final=True)
        self._stack.close()
        self._stream = None
        data = bytes(self._data)
        if self._path is not None:
            # 从 UTF-8 字符起点开始显示尾部，避免人为插入替代字符。
            offset = 0
            while offset < len(data) and data[offset] & 0xC0 == 0x80:
                offset += 1
            data = data[offset:]
        lines = self._newlines + (self._last_byte not in (None, 10))
        return data.decode("utf-8", errors="replace"), self._path, lines
