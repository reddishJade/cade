from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from cade.coding_agent.execution_modes import ExecutionMode
from cade.harness.agent_runtime import BusyMessageMode
from cade.harness.security import (
    FileGrantStore,
    InMemoryGrantStore,
    PermissionPolicy,
)
from cade.harness.session import SessionStore
from cade.harness.snapshot import SnapshotStore

from .app_contract import InteractionApp


@dataclass(frozen=True)
class Choice[T]:
    title: str
    value: T


class CommandOutput(Protocol):
    def write(self, text: str = "", *, end: str = "\n") -> None: ...

    def render(self, text: str) -> None: ...

    def select[T](
        self, title: str, choices: Sequence[Choice[T]], default: T | None = None
    ) -> T | None: ...


@dataclass
class InteractionState:
    mode: ExecutionMode = "act"
    busy_mode: BusyMessageMode = BusyMessageMode.FOLLOW_UP


@dataclass
class CommandContext:
    store: SessionStore
    app: InteractionApp
    output: CommandOutput
    host_command: Callable[[str, CommandContext], bool]
    state: InteractionState
    project_root: Path
    session_grant_store: InMemoryGrantStore | None = None
    permanent_grant_store: FileGrantStore | None = None
    static_policy: PermissionPolicy | None = None
    restricted_dirs: tuple[str, ...] = ()
    snapshot_store: SnapshotStore | None = None


CommandHandler = Callable[[str, CommandContext], bool]


COMMAND_GROUP_SESSION_LIFECYCLE = "Session Lifecycle"
COMMAND_GROUP_SESSION_BRANCH = "Session Branches"
COMMAND_GROUP_SESSION_ROLLBACK = "Session Rollback"
COMMAND_GROUP_MODE = "Mode Control"
COMMAND_GROUP_MODEL = "Model Configuration"
COMMAND_GROUP_AUTH = "Authentication"
COMMAND_GROUP_INFO = "Info Tools"
COMMAND_GROUP_EXIT = "Exit"

COMMAND_GROUP_ORDER: dict[str, int] = {
    COMMAND_GROUP_SESSION_LIFECYCLE: 1,
    COMMAND_GROUP_SESSION_BRANCH: 2,
    COMMAND_GROUP_SESSION_ROLLBACK: 3,
    COMMAND_GROUP_MODE: 4,
    COMMAND_GROUP_MODEL: 5,
    COMMAND_GROUP_AUTH: 6,
    COMMAND_GROUP_INFO: 7,
    COMMAND_GROUP_EXIT: 8,
}


@dataclass
class CommandEntry:
    handler: CommandHandler
    desc: str
    args_desc: str = ""
    accepts_args: bool = False
    visible: bool = True
    group: str = ""
    canonical: str | None = None


def command_names(registry: dict[str, CommandEntry]) -> tuple[str, ...]:
    """从注册表派生可补全命令名。"""
    return tuple(name for name, entry in registry.items() if entry.visible)


def generate_help_text(registry: dict[str, CommandEntry]) -> str:
    """从注册表按分组生成 HELP_TEXT。"""
    lines = ["Commands:"]
    groups: dict[str, list[tuple[str, CommandEntry]]] = {}
    for name, entry in registry.items():
        if not entry.visible:
            continue
        g = entry.group or ""
        groups.setdefault(g, []).append((name, entry))
    sorted_groups = sorted(groups, key=lambda g: COMMAND_GROUP_ORDER.get(g, 99))
    for group in sorted_groups:
        lines.append(f"\n  {group}:")
        for name, entry in groups[group]:
            lines.append(f"    {name:<11} {entry.desc}")
            if entry.args_desc:
                lines.append(f"    {name} {entry.args_desc}")
    return "\n".join(lines)
