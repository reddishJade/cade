"""Prompt 不应自动注入无法判定来源的 Git 状态。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from cade.harness.agent_runtime.prompting.builder import (
    PromptContext,
    SystemPromptBuilder,
)
from cade.harness.config import DEFAULT_PROMPT_MODULES


def test_default_prompt_does_not_include_git_worktree_state(tmp_path: Path) -> None:
    with patch("subprocess.run") as run:
        prompt = SystemPromptBuilder().build(
            PromptContext(project_root=tmp_path, registry=(), question="inspect")
        )

    assert "git_preflight" not in DEFAULT_PROMPT_MODULES
    assert "<git-preflight>" not in prompt
    run.assert_not_called()
