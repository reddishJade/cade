"""长期记忆的显式工具：两段式检索与经证据约束的经验写入。

两者都不调用模型：检索是 BM25 加锚点过滤，校验与新鲜度是本地确定性计算。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

from cade.agent.types import ToolInput, ToolSpec

from .experience import (
    EXPERIENCE_TYPE,
    Experience,
    ValidationEvidence,
    anchor_snapshot,
    git_head,
    parse_anchors,
    parse_experience,
)
from .hints import MemoryHintState
from .manager import (
    MemoryLayerFilter,
    MemoryManager,
    memory_write_rejection,
)
from .parsing import MemoryRecord

_LIMIT_DEFAULT = 3
_ANCHOR_EXAMPLE = "src/cade/harness/memory/manager.py"
_VERIFY_LIMIT = 200


def build_memory_tools(
    manager: MemoryManager,
    *,
    session_id_provider: Callable[[], str | None] | None = None,
    validation_provider: Callable[[], ValidationEvidence | None] | None = None,
    hint_state: MemoryHintState | None = None,
) -> tuple[ToolSpec, ...]:
    """构建 `recall`（索引/正文两段）与 `remember`（证据约束写入）。"""

    def search_memory(
        data: ToolInput,
        _on_update: Callable[[str], None] | None = None,
    ) -> str:
        memory_id = str(data.get("memory_id", "")).strip()
        if memory_id:
            record = manager.find_record(memory_id)
            if record is None:
                return f"No memory record with memory_id={memory_id!r}."
            if hint_state is not None:
                hint_state.mark_surfaced(memory_id)
            return manager.render_full_result(record)

        query = str(data.get("query", "")).strip()
        anchor = str(data.get("anchor", "")).strip() or None
        if not query and anchor is None:
            return "query, anchor, or memory_id is required"
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
            target = anchor or query
            return f"No memory matching {target!r}. Inspect MEMORY.md directly."
        lines = [manager.render_index_result(record) for record in records]
        if hint_state is not None:
            for record in records:
                hint_state.mark_surfaced(record.memory_id)
        return (
            "\n".join(lines)
            + "\nIndex only: call recall with memory_id=<id> to read one full record "
            "before changing code."
        )

    def remember_experience(
        data: ToolInput,
        _on_update: Callable[[str], None] | None = None,
    ) -> str:
        fields = {
            name: str(data.get(name, "")).strip()
            for name in ("title", "root_cause", "fix", "applies_when")
        }
        missing = [name for name, value in fields.items() if not value]
        if missing:
            return "missing required fields: " + ", ".join(missing)
        anchors = _text_items(data.get("anchors"))
        if not anchors:
            return "anchors must list at least one anchor"
        evidence = validation_provider() if validation_provider is not None else None
        if evidence is None:
            return (
                "No successful validation event in this session. Run the verification "
                "command (bash with purpose=validation) and record the experience "
                "afterwards, so the record points at a real observed result."
            )
        stamped = _stamped_evidence(manager, evidence, anchors, session_id_provider)
        block = _experience_block(fields, anchors, stamped)
        rejection = memory_write_rejection(
            block, layer="project", project_root=manager.root
        )
        if rejection:
            return f"Experience was not saved: {rejection}"
        if not manager.add_memory_block(block, layer="project"):
            return (
                "Experience was not saved: the title or body duplicates an existing "
                "record."
            )
        records = [
            record
            for record in manager.read_memory_records("project")
            if record.title == fields["title"]
        ]
        rendered = (
            manager.render_hint_packet(_require_experience(records[0]))
            if records
            else f"## {fields['title']}"
        )
        note = (
            ""
            if parse_anchors(", ".join(anchors)) and _has_auto_hint(anchors)
            else "\nNote: no file/dir/err anchor, so this record is recall-only."
        )
        return (
            f"Saved to {manager.memory_file}\n{rendered}\n"
            f"Evidence: validation={evidence.message_id} "
            f"exit_code={evidence.exit_code} verify={evidence.command}{note}"
        )

    return (
        ToolSpec(
            name="recall",
            description=(
                "Search durable project and user memory for prior rules, "
                "architecture decisions, verified facts, and recorded coding "
                "experiences. Results are an index; read one record with "
                "memory_id before acting on it."
            ),
            input_hint=(
                f'JSON: {{"query": "provider timeout", "limit": 3, '
                f'"anchor": "{_ANCHOR_EXAMPLE}"}} or '
                '{"memory_id": "mem_1a2b3c4d5e6f"}'
            ),
            handler=search_memory,
            schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "memory_id": {
                        "type": "string",
                        "description": "Stable id from an index line; returns one record.",
                    },
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
                "may have bitten this project before. Index lines are pointers: read "
                "the record with memory_id before relying on it."
            ),
        ),
        ToolSpec(
            name="remember",
            description=(
                "Persist one durable, expensive-to-learn coding experience after its "
                "verification succeeded: the problem, root cause, the fix that worked, "
                "and when it applies. The host stamps the validation event it points "
                "at, so run the verification first."
            ),
            input_hint=(
                'JSON: {"title": "fd single-file discovery mismatch", '
                '"root_cause": "directory-oriented discovery assumes traversal", '
                '"fix": "classify explicit file input before traversal", '
                '"applies_when": "fd backend with an explicit single-file path", '
                '"anchors": ["src/cade/tools/fd.py"]}'
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
                            "or `err=<error signature>`. Only file/dir/err anchors "
                            "can trigger automatic hints."
                        ),
                    },
                },
                "required": ["title", "root_cause", "fix", "applies_when", "anchors"],
                "additionalProperties": False,
            },
            prompt_snippet=(
                "After a verification command succeeds, record the lesson with "
                "remember in your next step. It stores only project memory and stamps "
                "the validation event, the anchor content snapshot and HEAD; do not "
                "store progress, guesses, or transcript."
            ),
        ),
    )


def _experience_block(
    fields: dict[str, str],
    anchors: list[str],
    evidence: list[str],
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
    evidence: ValidationEvidence,
    anchors: list[str],
    session_id_provider: Callable[[], str | None] | None,
) -> list[str]:
    """机械盖章机器已知的事实；模型无法通过 remember 伪造这些字段。"""
    stamped = [
        f"validation={evidence.message_id}",
        f"verify={_sanitize(evidence.command, _VERIFY_LIMIT)}",
        f"exit_code={evidence.exit_code}",
    ]
    session_id = session_id_provider() if session_id_provider is not None else None
    if session_id:
        stamped.append(f"session={session_id}")
    parsed = parse_anchors(", ".join(anchors))
    snapshot = anchor_snapshot(
        tuple(anchor.value for anchor in parsed if anchor.kind == "file"),
        manager.root,
    )
    if snapshot:
        stamped.append(f"anchor_state={snapshot}")
    revision = git_head(manager.root)
    if revision:
        stamped.append(f"commit={revision}")
    return stamped


def _has_auto_hint(anchors: list[str]) -> bool:
    return any(anchor.auto_hint for anchor in parse_anchors(", ".join(anchors)))


def _sanitize(value: str, limit: int) -> str:
    """证据用 `;` 分隔，命令里的分隔符与换行必须压平。"""
    text = " ".join(value.replace(";", " ").split())
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _text_items(value: object) -> list[str]:
    if isinstance(value, str):
        return [
            item.strip() for item in value.replace("\n", ",").split(",") if item.strip()
        ]
    if isinstance(value, list | tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


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
