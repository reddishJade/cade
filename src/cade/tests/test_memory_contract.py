"""保护项目记忆目录 (MemoryCatalog) 收集与安全持久化契约。

控制性回归使用真实 MemoryCatalogCollector 与 build_save_memory_tool。
断言记忆条目收集、软链接防御、有界字节截断以及乐观并发写锁契约。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from cade.agent.context import (
    ContextBlockSource,
    ContextCollectionInput,
    ContextPriority,
)
from cade.harness.memory.catalog import MemoryCatalogCollector
from cade.harness.memory.tools import build_save_memory_tool
from cade.harness.session import SessionHistory, SessionStore


def test_memory_catalog_collector_projection(tmp_path: Path) -> None:
    """验证 MemoryCatalogCollector 能够扫描 .cade/memory 目录下的有效 markdown 并生成受控上下文块。"""
    memory_dir = tmp_path / ".cade" / "memory"
    memory_dir.mkdir(parents=True)

    # 写入常规记忆文件
    (memory_dir / "architecture.md").write_text(
        "# System Architecture\nCade is built with strict layered boundaries.\n",
        encoding="utf-8",
    )
    (memory_dir / "testing.md").write_text(
        "# Testing Guidelines\nAlways write high-quality contract tests.\n",
        encoding="utf-8",
    )
    # 写入隐藏文件（应被忽略）
    (memory_dir / ".hidden.md").write_text(
        "# Hidden\nIgnored file.\n", encoding="utf-8"
    )

    collector = MemoryCatalogCollector(project_root=tmp_path)
    blocks = collector.collect(ContextCollectionInput(project_root=tmp_path))

    assert len(blocks) == 1
    block = blocks[0]
    assert block.source == ContextBlockSource.MEMORY
    assert block.priority == ContextPriority.CONTEXT
    assert "System Architecture" in block.content
    assert "Testing Guidelines" in block.content
    assert "Hidden" not in block.content


def test_memory_catalog_symlink_defense(tmp_path: Path) -> None:
    """验证当 .cade 或 .cade/memory 为符号链接时，出于安全防护直接返回空目录，防范目录逃逸。"""
    real_outside_dir = tmp_path.parent / "outside_memory"
    real_outside_dir.mkdir(exist_ok=True)
    (real_outside_dir / "secret.md").write_text(
        "# Secret\nExfiltrated data\n", encoding="utf-8"
    )

    cade_dir = tmp_path / ".cade"
    cade_dir.mkdir(parents=True)
    memory_symlink = cade_dir / "memory"
    try:
        memory_symlink.symlink_to(real_outside_dir, target_is_directory=True)
    except OSError as exc:
        if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
            pytest.skip("The current Windows account cannot create symbolic links")
        raise

    collector = MemoryCatalogCollector(project_root=tmp_path)
    blocks = collector.collect(ContextCollectionInput(project_root=tmp_path))
    assert blocks == []


def test_save_memory_tool_sources_and_optimistic_concurrency(tmp_path: Path) -> None:
    """验证保存记忆必须包含真实存在的会话事实引用 (sources)，并具备乐观并发保护。"""
    sessions_dir = tmp_path / ".cade" / "sessions"

    store = SessionStore(sessions_dir, project_root=tmp_path)
    entry_id = store.append("event", {"note": "Ground truth observation"})

    history = SessionHistory(sessions_dir, project_root=tmp_path)
    history.set_session_id(store.session_id)
    tool_spec = build_save_memory_tool(project_root=tmp_path, history=history)

    target_rel = ".cade/memory/notes.md"
    initial_text = "# Project Notes\nFirst draft."
    valid_sources = [{"session_id": store.session_id, "entry_id": entry_id}]

    # 1. 缺少显式 sources 引用必须被直接拒绝
    with pytest.raises(
        ValueError, match="sources must contain explicit history references"
    ):
        tool_spec.handler({"path": target_rel, "markdown": initial_text}, None)

    # 2. 引用不存在的 entry_id 必须被拒绝
    with pytest.raises(ValueError, match="History source not found"):
        tool_spec.handler(
            {
                "path": target_rel,
                "markdown": initial_text,
                "sources": [
                    {"session_id": store.session_id, "entry_id": "nonexistent"}
                ],
            },
            None,
        )

    # 3. 提供真实有效引用，首次创建成功
    tool_spec.handler(
        {
            "path": target_rel,
            "markdown": initial_text,
            "sources": valid_sources,
        },
        None,
    )
    saved_file = tmp_path / target_rel
    assert saved_file.exists()
    assert initial_text in saved_file.read_text(encoding="utf-8")
    assert f"entry_id={entry_id}" in saved_file.read_text(encoding="utf-8")

    # 4. 尝试用错误的 expected_content 覆盖，必须抛出 ValueError 并拒绝
    with pytest.raises(ValueError, match="Memory changed or already exists"):
        tool_spec.handler(
            {
                "path": target_rel,
                "markdown": "# Updated Notes",
                "sources": valid_sources,
                "expected_content": "wrong previous content",
            },
            None,
        )

    # 5. 提供与当前磁盘内容完全一致的 expected_content，覆盖成功
    current_content = saved_file.read_text(encoding="utf-8")
    updated_text = "# Project Notes\nSecond revised draft."
    tool_spec.handler(
        {
            "path": target_rel,
            "markdown": updated_text,
            "sources": valid_sources,
            "expected_content": current_content,
        },
        None,
    )
    assert updated_text in saved_file.read_text(encoding="utf-8")
