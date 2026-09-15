"""上下文换窗斜杠命令测试。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from xcode.agent.messages import (
    AgentMessage,
    AssistantMessage,
    SystemMessage,
    UserMessage,
)
from xcode.agent.types import TextContent
from xcode.cli.commands import CommandContext
from xcode.cli.repl_commands import (
    COMMAND_NAMES,
    COMMAND_REGISTRY,
    cmd_compact,
    cmd_rollover,
)
from xcode.harness.agent_runtime.context_window import ContextWindowRollover


class _Agent:
    def __init__(self, messages: list[AgentMessage]) -> None:
        self._messages = messages
        self.context_rollover = ContextWindowRollover()
        self.loaded: list[AgentMessage] | None = None

    def history_messages(self) -> list[AgentMessage]:
        return self._messages

    def load_history(self, messages: list[AgentMessage]) -> None:
        self.loaded = messages
        self._messages = messages


class _App:
    def __init__(self, agent: _Agent) -> None:
        self.agent = agent
        self.resets: list[dict[str, Any]] = []

    def record_context_window_reset(self, **payload: Any) -> str:
        self.resets.append(payload)
        return "entry-1"


def _messages() -> list[AgentMessage]:
    return [
        SystemMessage(content="system"),
        UserMessage(content="old goal"),
        AssistantMessage(content=[TextContent(text="old result")]),
        UserMessage(content="latest goal"),
        AssistantMessage(content=[TextContent(text="latest result")]),
    ]


def _context(project_root: Path) -> tuple[CommandContext, _Agent, _App]:
    agent = _Agent(_messages())
    app = _App(agent)
    context = cast(
        CommandContext,
        SimpleNamespace(app=app, project_root=project_root),
    )
    return context, agent, app


def _render(messages: list[AgentMessage] | None) -> str:
    assert messages is not None
    return "\n".join(message.model_dump_json() for message in messages)


def test_compact_rolls_over_and_retains_latest_turn(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    context, agent, app = _context(tmp_path)

    cmd_compact("/compact", context)

    rendered = _render(agent.loaded)
    assert "old goal" not in rendered
    assert "old result" not in rendered
    assert "latest goal" in rendered
    assert "latest result" in rendered
    assert len(app.resets) == 1
    assert "Retained the latest turn" in capsys.readouterr().out


def test_rollover_requires_working_note(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    context, agent, app = _context(tmp_path)

    cmd_rollover("/rollover", context)

    assert agent.loaded is None
    assert app.resets == []
    assert "Write NOTE.md" in capsys.readouterr().out


def test_rollover_starts_clean_window_with_working_note(tmp_path: Path) -> None:
    (tmp_path / "NOTE.md").write_text("Next: run tests.\n", encoding="utf-8")
    context, agent, app = _context(tmp_path)

    cmd_rollover("/rollover", context)

    rendered = _render(agent.loaded)
    assert "old goal" not in rendered
    assert "latest goal" not in rendered
    assert "latest result" not in rendered
    assert len(app.resets) == 1


def test_rollover_force_bypasses_working_note(tmp_path: Path) -> None:
    context, agent, app = _context(tmp_path)

    cmd_rollover("/rollover --force", context)

    assert agent.loaded is not None
    assert len(app.resets) == 1


def test_context_command_registry_keeps_compact_and_hides_old_alias() -> None:
    assert "/compact" in COMMAND_NAMES
    assert "/rollover" in COMMAND_NAMES
    assert "/new-context" not in COMMAND_NAMES
    assert COMMAND_REGISTRY["/new-context"].canonical == "/rollover"
