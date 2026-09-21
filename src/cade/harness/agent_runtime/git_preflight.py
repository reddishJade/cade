from __future__ import annotations

import subprocess
from pathlib import Path

"""每轮任务开始前注入的 Git 工作区基线。"""

# Git 输出限制
MAX_SECTION_CHARS = 6_000  # Git 输出截断：避免大 diff 撑爆 system prompt

_STATUS_PATHS = (
    ".",
    ":(exclude).cade/sessions",
    ":(exclude).cade/snapshots",
)


def build_git_preflight(project_root: Path) -> str:
    """捕获轻量 Git 基线；调用方负责在 session 生命周期内冻结结果。"""
    status = _run_git(
        project_root,
        "status",
        "--short",
        "--untracked-files=normal",
        "--",
        *_STATUS_PATHS,
    )
    if status is None:
        return "<git-preflight>\nstatus: unavailable\n</git-preflight>"

    last_commit = _run_git(project_root, "log", "-1", "--format=%h %s")
    lines = ["<git-preflight>"]
    clean = not status.strip()
    lines.append("status:")
    lines.append(_truncate(status.strip()) if not clean else "clean")
    if last_commit:
        lines.append("\nlast_commit:")
        lines.append(_truncate(last_commit.strip()))
    if not clean:
        lines.append(
            "\nThis is the working-tree baseline captured when the agent runtime "
            "started. Treat these paths as user-owned. Later task changes appear "
            "separately in active_diff; do not reclassify them as baseline."
        )
    lines.append("</git-preflight>")
    return "\n".join(lines)


def _run_git(project_root: Path, *args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=project_root,
            capture_output=True,
            check=False,
            text=True,
            errors="replace",
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout


def _truncate(text: str) -> str:
    if len(text) <= MAX_SECTION_CHARS:
        return text
    keep = MAX_SECTION_CHARS - 80
    return text[:keep] + f"\n[... truncated {len(text) - keep} chars ...]"
