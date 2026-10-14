"""保护 Session 大文本工件 (Artifact) 卸载、有界预览与无损回读契约。

控制性回归使用真实 offload_large_tool_result 与 resolve_tool_result_artifact。
在独立 tmp_path 空间验证 32KB 内联阈值、大文本落盘、引用防篡改校验及无损还原。
"""

from __future__ import annotations

from pathlib import Path

from cade.harness.session.artifacts import (
    TOOL_RESULT_INLINE_BYTES,
    offload_large_tool_result,
    resolve_history_event_content,
    resolve_tool_result_artifact,
)


def test_small_tool_result_stays_inline(tmp_path: Path) -> None:
    """验证小于等于 32KB 的工具输出直接内联在事件中，不产生多余的磁盘工件文件。"""
    small_content = "Small tool output result.\n" * 100
    assert len(small_content.encode("utf-8")) < TOOL_RESULT_INLINE_BYTES

    preview, reference = offload_large_tool_result(tmp_path, small_content)
    assert reference is None
    assert preview == small_content
    # 确认没有工件文件被创建
    assert list(tmp_path.glob("*.txt")) == []


def test_large_tool_result_offloading_and_lossless_recovery(tmp_path: Path) -> None:
    """验证超过 32KB 的工具结果原子写入不可变工件，并能根据防篡改元数据精确完整回读。"""
    # 构造约 60KB 的大文本
    line = "Critical logs and stdout data record index.\n"
    large_content = line * 1500
    assert len(large_content.encode("utf-8")) > TOOL_RESULT_INLINE_BYTES

    preview, reference = offload_large_tool_result(tmp_path, large_content)
    assert reference is not None
    assert reference["schema_version"] == 1
    assert "artifact_id" in reference
    assert reference["bytes"] == len(large_content.encode("utf-8"))
    assert reference["chars"] == len(large_content)

    # 验证热上下文使用的预览文本被安全截断并包含提示
    assert len(preview) < len(large_content)
    assert "artifact_id=" in preview

    # 验证磁盘上的工件文件存在
    artifact_id = str(reference["artifact_id"])
    artifact_path = tmp_path / f"{artifact_id}.txt"
    assert artifact_path.exists()

    # 验证 resolve_tool_result_artifact 无损精确还原
    recovered = resolve_tool_result_artifact(tmp_path, reference)
    assert recovered == large_content


def test_artifact_tamper_detection_and_missing_handling(tmp_path: Path) -> None:
    """验证工件元数据篡改（字节长度不匹配）或文件丢失时，安全返回 None 而不导致异常崩溃。"""
    content = "A" * (TOOL_RESULT_INLINE_BYTES + 1000)
    _, reference = offload_large_tool_result(tmp_path, content)
    assert reference is not None

    # 1. 元数据中的 bytes 被篡改，无法通过校验
    tampered_ref = dict(reference)
    tampered_ref["bytes"] = 999999
    assert resolve_tool_result_artifact(tmp_path, tampered_ref) is None

    # 2. 工件文件被删除
    artifact_path = tmp_path / f"{reference['artifact_id']}.txt"
    artifact_path.unlink()
    assert resolve_tool_result_artifact(tmp_path, reference) is None


def test_resolve_history_event_content_integration(tmp_path: Path) -> None:
    """验证会话历史事件中包含 content_artifact 引用时，resolve_history_event_content 能够自动解析。"""
    content = "Detailed traces " * 3000
    _, reference = offload_large_tool_result(tmp_path, content)
    assert reference is not None

    event_payload = {
        "type": "tool_result",
        "data": {
            "content": "truncated preview",
            "content_artifact": reference,
        },
    }

    resolved = resolve_history_event_content(event_payload, tmp_path)
    assert isinstance(resolved, dict)
    data = resolved.get("data")
    assert isinstance(data, dict)
    assert data.get("content") == content
    assert "content_artifact" in data
