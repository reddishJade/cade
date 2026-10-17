"""编码产品工具注册表构建器。

产品层决定工具分组、构建方式和所需的运行时参数。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING

from cade.agent.types import ToolSpec
from cade.coding_agent.tools import (
    build_bash_tool,
    build_edit_tool,
    build_glob_tools,
    build_grep_tool,
    build_question_tool,
    build_read_tool,
    build_webfetch_tool,
    build_websearch_tool,
    build_write_tool,
)
from cade.harness.execution_env import Shell

if TYPE_CHECKING:
    from cade.coding_agent.tools import ShellSpec
    from cade.harness.skills import SkillRegistry


def build_project_scoped_registry(
    project_root: Path,
    shell_spec: ShellSpec,
    cancel_event: threading.Event | None = None,
    shell: Shell | None = None,
    skill_registry: SkillRegistry | None = None,
) -> tuple[ToolSpec, ...]:
    registry: tuple[ToolSpec, ...] = ()
    registry += (
        build_read_tool(
            project_root,
            cancel_event=cancel_event,
        ),
    )
    registry += (
        build_write_tool(project_root, cancel_event=cancel_event),
        build_edit_tool(project_root, cancel_event=cancel_event),
    )
    registry += build_glob_tools(project_root, cancel_event=cancel_event)
    registry += (build_grep_tool(project_root, cancel_event=cancel_event),)
    registry += (build_webfetch_tool(), build_websearch_tool())
    registry += (build_question_tool(),)
    registry += (
        build_bash_tool(
            project_root,
            shell_spec=shell_spec,
            cancel_event=cancel_event,
            shell=shell,
        ),
    )
    if skill_registry is not None and skill_registry.available_names():
        from cade.harness.skills import build_load_skill_tool

        registry += (build_load_skill_tool(skill_registry),)
    return registry
