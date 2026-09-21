"""Git preflight 纯函数单元测试。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import call, patch

from cade.harness.agent_runtime.git_preflight import (
    _STATUS_PATHS,
    MAX_SECTION_CHARS,
    _truncate,
    build_git_preflight,
)
from cade.harness.agent_runtime.prompting.builder import (
    PromptContext,
    VolatileRegionBuilder,
)


class TestTruncate:
    def test_short_text(self) -> None:
        assert _truncate("hello") == "hello"

    def test_long_text_truncated(self) -> None:
        text = "x" * (MAX_SECTION_CHARS + 200)
        result = _truncate(text)
        assert len(result) <= MAX_SECTION_CHARS
        assert "truncated" in result


def test_preflight_uses_lightweight_git_baseline(tmp_path: Path) -> None:
    with patch(
        "cade.harness.agent_runtime.git_preflight._run_git",
        side_effect=[" M src/example.py\n", "abc1234 current subject\n"],
    ) as run_git:
        result = build_git_preflight(tmp_path)

    assert "M src/example.py" in result
    assert "abc1234 current subject" in result
    assert "active_diff" in result
    assert run_git.call_args_list == [
        call(
            tmp_path,
            "status",
            "--short",
            "--untracked-files=normal",
            "--",
            *_STATUS_PATHS,
        ),
        call(tmp_path, "log", "-1", "--format=%h %s"),
    ]


def test_prompt_builder_freezes_git_baseline(tmp_path: Path) -> None:
    builder = VolatileRegionBuilder()
    context = PromptContext(project_root=tmp_path, registry=(), question="")

    with patch(
        "cade.harness.agent_runtime.prompting.builder.build_git_preflight",
        side_effect=["baseline-one", "baseline-two"],
    ) as build:
        first = builder.build(context, {"git_preflight"})
        second = builder.build(context, {"git_preflight"})

    assert first == ["baseline-one"]
    assert second == ["baseline-one"]
    build.assert_called_once_with(tmp_path.resolve())
