"""配置解析。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cade.harness.config import (
    AgentConfig,
    CadeRuntimeConfig,
    discover_runtime_config,
    resolve_config_path,
)


def default_env_files(project_root: Path) -> tuple[Path, ...]:
    """默认的 .env 查找位置：包目录、项目根目录与 cade/ 子目录。"""
    _pkg_root = Path(__file__).resolve().parent.parent.parent
    return (
        _pkg_root / ".env",
        project_root / ".env",
        project_root / "cade" / ".env",
    )


@dataclass(frozen=True)
class ResolvedConfig:
    runtime_config: CadeRuntimeConfig
    agent_config: AgentConfig
    skills_dir: Path | None
    audit_path: Path | None
    env_files: tuple[Path, ...]


def resolve_config(
    project_root: Path,
    env_files: tuple[Path, ...] | None,
    agent_config: AgentConfig | None,
    skills_dir: Path | None,
    audit_path: Path | None,
    runtime_config: CadeRuntimeConfig | None,
) -> ResolvedConfig:
    runtime_config = runtime_config or discover_runtime_config(project_root)
    agent_config = agent_config or runtime_config.agent
    skills_dir = skills_dir or resolve_config_path(
        project_root, runtime_config.paths.skills_dir
    )
    audit_path = audit_path or resolve_config_path(
        project_root, runtime_config.observability.audit_path
    )
    env_files = env_files or default_env_files(project_root)
    return ResolvedConfig(
        runtime_config=runtime_config,
        agent_config=agent_config,
        skills_dir=skills_dir,
        audit_path=audit_path,
        env_files=env_files,
    )
