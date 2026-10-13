"""从实际提供的工具生成操作指南。"""

from __future__ import annotations

from cade.agent.types import ToolSpec


def build_tool_guidelines(registry: tuple[ToolSpec, ...]) -> str:
    """按注册顺序渲染去重后的工具指南。"""
    guidelines: list[str] = []
    seen: set[str] = set()
    for tool in registry:
        for guideline in tool.prompt_guidelines:
            normalized = guideline.strip()
            if normalized and normalized not in seen:
                seen.add(normalized)
                guidelines.append(f"- {normalized}")
    return "\n".join(guidelines)
