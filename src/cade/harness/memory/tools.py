"""长期记忆的显式读工具与经验写入工具。

两者都不调用模型：校验是确定性的，检索是 BM25 加锚点过滤。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import cast

from cade.agent.types import ToolInput, ToolSpec

from .experience import EXPERIENCE_TYPE, Experience, git_head, parse_experience
from .manager import (
    MemoryLayer,
    MemoryLayerFilter,
    MemoryManager,
    memory_write_rejection,
)
from .parsing import MemoryRecord

_LIMIT_DEFAULT = 3
_ANCHOR_HINT = "src/cade/harness/memory/manager.py"


def build_memory_tools(
    manager: MemoryManager,
    *,
    session_id_provider: Callable[[], str | None] | None = None,
) -> tuple[ToolSpec, ...]:
    """构建显式按需调用的 `recall` 与经验写入工具 `remember`。"""

    def search_memory(
        data: ToolInput,
        _on_update: Callable[[str], None] | None = None,
    ) -> str:
        query = str(data.get("query", "")).strip()
        anchor = str(data.get("anchor", "")).strip() or None
        if not query and anchor is None:
            return "query or anchor is required"
        layer = str(data.get("layer", "all"))
        if layer not in {"all", "project", "user"}:
            return "layer must be one of: all, project, user"
        limit = _parse_limit(data.get("limit", _LIMIT_DEFAULT))
        scope = str(data.get("scope", "")).strip() or None
        records = manager.search_memory_records(
            query,
            limit=limit,
            layer=cast(MemoryLayerFilter, layer),
            scope=scope,
            anchor=anchor,
        )
        if not records:
            if anchor:
                return (
                    f"No experience anchored at {anchor!r}. Try fewer terms or "
                    "inspect MEMORY.md directly."
                )
            return (
                f"No memory matching {query!r}. Try fewer terms or inspect "
                "MEMORY.md directly."
            )
        return "\n\n".join(manager.render_search_result(record) for record in records)

    def remember_experience(
        data: ToolInput,
        _on_update: Callable[[str], None] | None = None,
    ) -> str:
        layer = str(data.get("layer", "project"))
        if layer not in {"project", "user"}:
            return "layer must be one of: project, user"
        fields = {
            name: str(data.get(name, "")).strip()
            for name in ("title", "root_cause", "fix", "applies_when")
        }
        missing = [name for name, value in fields.items() if not value]
        if missing:
            return "missing required fields: " + ", ".join(missing)
        anchors = _text_items(data.get("anchors"))
        if not anchors:
            return "anchors must list at least one file or dir anchor"
        evidence = _evidence_items(data.get("evidence"))
        evidence.extend(_stamped_evidence(manager, session_id_provider))
        if not evidence:
            return "evidence must record at least one pointer, such as test=<command>"
        block = _experience_block(fields, anchors, evidence)
        memory_layer = cast(MemoryLayer, layer)
        rejection = memory_write_rejection(
            block, layer=memory_layer, project_root=manager.root
        )
        if rejection:
            return f"Experience was not saved: {rejection}"
        if not manager.add_memory_block(block, layer=memory_layer):
            return (
                "Experience was not saved: the title or body duplicates an existing "
                "record."
            )
        path = manager.memory_file if layer == "project" else manager.user_memory_file
        records = [
            record
            for record in manager.read_memory_records(memory_layer)
            if record.title == fields["title"]
        ]
        rendered = (
            manager.render_hint_packet(_require_experience(records[0]))
            if records
            else f"## {fields['title']}"
        )
        return f"Saved to {path}\n{rendered}"

    return (
        ToolSpec(
            name="recall",
            description=(
                "Search durable project and user memory for prior rules, "
                "architecture decisions, verified facts, and recorded coding "
                "experiences. Pass anchor to find experiences recorded for a file."
            ),
            input_hint=(
                f'JSON: {{"query": "provider timeout", "limit": 3, '
                f'"anchor": "{_ANCHOR_HINT}"}}'
            ),
            handler=search_memory,
            schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10,
                        "default": _LIMIT_DEFAULT,
                    },
                    "scope": {"type": "string"},
                    "anchor": {
                        "type": "string",
                        "description": (
                            "Repository-relative path; only experiences declaring "
                            "this anchor are returned."
                        ),
                    },
                    "layer": {
                        "type": "string",
                        "enum": ["all", "project", "user"],
                        "default": "all",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
            prompt_snippet=(
                "Use recall before asking the user to repeat prior project decisions "
                "or constraints, and with anchor=<path> before changing a file that "
                "may have bitten this project before."
            ),
        ),
        ToolSpec(
            name="remember",
            description=(
                "Persist one durable, expensive-to-learn coding experience: the "
                "problem, its root cause, the fix that worked, and when it applies. "
                "Record it in the same step as your final verification, never task "
                "progress or restated transcript."
            ),
            input_hint=(
                'JSON: {"title": "fd single-file discovery mismatch", '
                '"root_cause": "directory-oriented discovery assumes traversal", '
                '"fix": "classify explicit file input before traversal", '
                '"applies_when": "fd backend with an explicit single-file path", '
                '"anchors": ["src/cade/tools/fd.py"], '
                '"evidence": ["test=uv run pytest src/cade/tests/test_fd.py -q"]}'
            ),
            handler=remember_experience,
            schema={
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "root_cause": {"type": "string"},
                    "fix": {"type": "string"},
                    "applies_when": {"type": "string"},
                    "anchors": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "description": (
                            "Repository-relative paths, `dir=<path>`, `sym=<name>`, "
                            "or `err=<error signature>`."
                        ),
                    },
                    "evidence": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Pointers such as test=<command>, message_id=<history id>, "
                            "pr=<url>. Commit and session are stamped automatically."
                        ),
                    },
                    "layer": {
                        "type": "string",
                        "enum": ["project", "user"],
                        "default": "project",
                    },
                },
                "required": [
                    "title",
                    "root_cause",
                    "fix",
                    "applies_when",
                    "anchors",
                ],
                "additionalProperties": False,
            },
            prompt_snippet=(
                "After a costly fix, call remember with the root cause, the fix, when "
                "it applies, and repo-relative anchors. Do not store progress, "
                "guesses, or transcript excerpts."
            ),
        ),
    )


def _experience_block(
    fields: dict[str, str],
    anchors: Sequence[str],
    evidence: Sequence[str],
) -> str:
    return "\n".join(
        (
            f"## {fields['title']}",
            f"type: {EXPERIENCE_TYPE}",
            f"root_cause: {fields['root_cause']}",
            f"fix: {fields['fix']}",
            f"applies_when: {fields['applies_when']}",
            f"anchors: {', '.join(anchors)}",
            f"evidence: {'; '.join(evidence)}",
            "",
        )
    )


def _stamped_evidence(
    manager: MemoryManager,
    session_id_provider: Callable[[], str | None] | None,
) -> list[str]:
    """机械盖章机器已知的溯源，避免模型编造。"""
    stamped: list[str] = []
    revision = git_head(manager.root)
    if revision:
        stamped.append(f"commit={revision}")
    session_id = session_id_provider() if session_id_provider is not None else None
    if session_id:
        stamped.append(f"session={session_id}")
    return stamped


def _text_items(value: object) -> list[str]:
    if isinstance(value, str):
        return [
            item.strip() for item in value.replace("\n", ",").split(",") if item.strip()
        ]
    if isinstance(value, list | tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _evidence_items(value: object) -> list[str]:
    items = _text_items(value)
    return [" ".join(item.split()) for item in items]


def _require_experience(record: MemoryRecord) -> Experience:
    experience = parse_experience(record)
    if experience is None:  # pragma: no cover - 写入前已通过同一套校验
        raise ValueError("stored record is not a valid experience")
    return experience


def _parse_limit(value: object) -> int:
    if not isinstance(value, (str, int, float)):
        return _LIMIT_DEFAULT
    try:
        parsed = int(value)
    except (OverflowError, ValueError):
        return _LIMIT_DEFAULT
    return min(max(parsed, 1), 10)
