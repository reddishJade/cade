from __future__ import annotations

from dataclasses import dataclass

# 输出限制：平衡 LLM 上下文窗口利用率与响应速度
DEFAULT_MAX_LINES = 2000  # 默认行数限制：约 100K tokens
DEFAULT_MAX_BYTES = 50 * 1024  # 默认字节限制：50KB
GREP_MAX_LINE_LENGTH = 500  # grep 单行限制：避免二进制文件污染输出


@dataclass
class TruncationResult:
    content: str
    truncated: bool
    total_lines: int
    output_lines: int
    max_bytes: int


def _split_lines(content: str) -> list[str]:
    if not content:
        return []
    lines = content.split("\n")
    if content.endswith("\n"):
        lines.pop()
    return lines


def truncate_tail(
    content: str,
    max_lines: int = DEFAULT_MAX_LINES,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> TruncationResult:
    """按行数和 UTF-8 字节上限保留尾部，必要时截取末行。"""
    lines = _split_lines(content)
    total_lines = len(lines)
    if not lines or (
        total_lines <= max_lines and len(content.encode("utf-8")) <= max_bytes
    ):
        return TruncationResult(
            content=content,
            truncated=False,
            total_lines=total_lines,
            output_lines=total_lines,
            max_bytes=max_bytes,
        )

    output: list[str] = []
    output_bytes = 0
    truncated = False
    for line in reversed(lines):
        if len(output) >= max_lines:
            truncated = True
            break
        line_bytes = len(line.encode("utf-8")) + (1 if output else 0)
        if output_bytes + line_bytes > max_bytes:
            if not output:
                buf = line.encode("utf-8")
                if len(buf) > max_bytes:
                    start = len(buf) - max_bytes
                    while start < len(buf) and (buf[start] & 0xC0) == 0x80:
                        start += 1
                    output.append(buf[start:].decode("utf-8"))
            truncated = True
            break
        output.append(line)
        output_bytes += line_bytes

    output.reverse()
    return TruncationResult(
        content="\n".join(output),
        truncated=truncated or len(output) < total_lines,
        total_lines=total_lines,
        output_lines=len(output),
        max_bytes=max_bytes,
    )


def truncate_line(line: str, max_chars: int = GREP_MAX_LINE_LENGTH) -> tuple[str, bool]:
    if len(line) <= max_chars:
        return (line, False)
    return (line[:max_chars] + "... [truncated]", True)
