"""session 大文本 artifact 存储。"""

from __future__ import annotations

import os
import re
from pathlib import Path
from uuid import uuid4

from .types import JsonValue

TOOL_RESULT_INLINE_BYTES = 32 * 1024
TOOL_RESULT_PREVIEW_CHARS = 8_000

_ARTIFACT_SCHEMA_VERSION = 1
_PRIVATE_FILE_MODE = 0o600
_ARTIFACT_ID = re.compile(r"^[0-9a-f]{32}$")


def offload_large_tool_result(
    artifacts_dir: Path,
    content: str,
) -> tuple[str, dict[str, JsonValue] | None]:
    """大结果写入 artifact，返回供热上下文使用的有界预览。"""
    normalized = _normalize_text(content)
    encoded = normalized.encode("utf-8")
    if len(encoded) <= TOOL_RESULT_INLINE_BYTES:
        return normalized, None

    artifact_id = uuid4().hex
    path = artifacts_dir / f"{artifact_id}.txt"
    _write_immutable(path, encoded)
    reference: dict[str, JsonValue] = {
        "schema_version": _ARTIFACT_SCHEMA_VERSION,
        "artifact_id": artifact_id,
        "bytes": len(encoded),
        "chars": len(normalized),
    }
    return _preview(normalized, reference), reference


def resolve_tool_result_artifact(
    artifacts_dir: Path | None,
    reference: object,
) -> str | None:
    """校验引用与文件内容后读取完整 tool result。"""
    if artifacts_dir is None or not isinstance(reference, dict):
        return None
    if reference.get("schema_version") != _ARTIFACT_SCHEMA_VERSION:
        return None
    artifact_id = reference.get("artifact_id")
    expected_bytes = reference.get("bytes")
    expected_chars = reference.get("chars")
    if (
        not isinstance(artifact_id, str)
        or not _ARTIFACT_ID.fullmatch(artifact_id)
        or not isinstance(expected_bytes, int)
        or expected_bytes < 0
        or not isinstance(expected_chars, int)
        or expected_chars < 0
    ):
        return None
    try:
        payload = (artifacts_dir / f"{artifact_id}.txt").read_bytes()
        content = payload.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    if len(payload) != expected_bytes or len(content) != expected_chars:
        return None
    return content


def resolve_history_event_content(
    content: object,
    artifacts_dir: Path | None,
) -> object:
    """为 history 检索还原已外置的 tool result 正文。"""
    if not isinstance(content, dict) or content.get("type") != "tool_result":
        return content
    data = content.get("data")
    if not isinstance(data, dict):
        return content
    resolved = resolve_tool_result_artifact(
        artifacts_dir,
        data.get("content_artifact"),
    )
    if resolved is None:
        return content
    hydrated_data = dict(data)
    hydrated_data["content"] = resolved
    hydrated = dict(content)
    hydrated["data"] = hydrated_data
    return hydrated


def _write_immutable(path: Path, payload: bytes) -> None:
    """原子写入私有 artifact。"""
    temporary = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        _PRIVATE_FILE_MODE,
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            path.chmod(_PRIVATE_FILE_MODE)
    finally:
        temporary.unlink(missing_ok=True)


def _preview(content: str, reference: dict[str, JsonValue]) -> str:
    head_chars = TOOL_RESULT_PREVIEW_CHARS * 5 // 8
    tail_chars = TOOL_RESULT_PREVIEW_CHARS - head_chars
    omitted = max(len(content) - TOOL_RESULT_PREVIEW_CHARS, 0)
    marker = (
        "\n\n[...full tool result stored as an artifact: "
        f"{omitted} characters omitted, artifact_id={reference['artifact_id']}; "
        "use history read to retrieve the exact content...]\n\n"
    )
    return content[:head_chars] + marker + content[-tail_chars:]


def _normalize_text(value: str) -> str:
    """生成可稳定写入 UTF-8 的正文，并合并有效 UTF-16 代理对。"""
    if not any("\ud800" <= char <= "\udfff" for char in value):
        return value
    return value.encode("utf-16-le", errors="surrogatepass").decode(
        "utf-16-le",
        errors="replace",
    )
