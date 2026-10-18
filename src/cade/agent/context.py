"""结构化上下文块收集、组装与管理。"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from pathlib import Path
from typing import Literal, Protocol

from cade.agent._codec import convert_to_llm
from cade.agent._context_window import estimate_tokens, estimate_wire_message_tokens
from cade.agent.messages import (
    AgentMessage,
    ContextMessage,
    SystemMessage,
    UserMessage,
)
from cade.agent.types import AgentTool, materialize_json_mapping

logger = logging.getLogger(__name__)


# ── 来源枚举 ──


class ContextBlockSource(StrEnum):
    INSTRUCTION = "instruction"
    SKILL = "skill"
    NOTES = "notes"
    MEMORY = "memory"
    RECENT_VALIDATION = "recent_validation"
    ENVIRONMENT = "environment"
    PERMISSIONS = "permissions"
    MODE = "mode"


# ── 注入目标枚举 ──


class ContextBlockTarget(StrEnum):
    SYSTEM = "system"
    USER_CONTEXT = "user_context"


# ── 优先级枚举 ──


class ContextPriority(IntEnum):
    """仅决定稳定排序：指令先于事实上下文，不决定是否纳入请求。"""

    INSTRUCTION = 0
    CONTEXT = 10


# ── 上下文块 ──


@dataclass
class ContextBlock:
    """收集方已选定的上下文；组装时完整纳入，不参与预算淘汰。"""

    source: ContextBlockSource
    priority: ContextPriority
    content: str
    target: ContextBlockTarget = ContextBlockTarget.USER_CONTEXT
    block_id: str = ""
    provenance: str = ""
    truncated: bool = False
    truncation_reason: str | None = None
    scope: Literal["user", "project", "runtime"] = "project"

    def get_token_count(self) -> int:
        return estimate_tokens(self.content)


# ── 收集输入 ──


@dataclass
class ContextCollectionInput:
    system_prompt: str = ""
    messages: list[AgentMessage] = field(default_factory=list)
    tools: list[AgentTool] = field(default_factory=list)
    project_root: Path | None = None
    cwd: Path | None = None
    state: dict[str, object] = field(default_factory=dict)


# ── 组装输入/输出 ──


@dataclass
class ContextAssemblyInput:
    system_prompt: str = ""
    messages: list[AgentMessage] = field(default_factory=list)
    tools: list[AgentTool] = field(default_factory=list)
    context_blocks: list[ContextBlock] = field(default_factory=list)
    token_budget: int = 0
    calibrate_tokens: Callable[[list[AgentMessage], int], int] | None = None


@dataclass
class ContextAssemblyResult:
    messages: list[AgentMessage] = field(default_factory=list)
    # 仅本次新增的事实；发送前提交，纯组装不修改历史。
    context_message: ContextMessage | None = None
    blocks_used: list[ContextBlock] = field(default_factory=list)
    total_tokens: int = 0
    token_budget: int = 0
    budget_remaining: int = 0
    base_tokens: int = 0


# ── 请求级上下文 section ──


@dataclass(frozen=True)
class ContextSection:
    """每次请求直接收集完整动态上下文块。"""

    section_id: str
    collect: Callable[[ContextCollectionInput], list[ContextBlock]]


# ── 收集器协议 ──


class ContextAssemblyError(RuntimeError):
    """上下文无法完整收集或无法安全发送。"""


class ContextCollector(Protocol):
    def collect(self, input: ContextCollectionInput) -> list[ContextBlock]: ...


# ── 收集器注册表 ──


class ContextCollectorRegistry:
    def __init__(self) -> None:
        self._sections: list[ContextSection] = []

    def register_section(self, section: ContextSection) -> None:
        """注册每次请求重新收集的动态 section。"""
        self._sections.append(section)

    def freeze(self) -> FrozenContextCollectorRegistry:
        """发布不可再注册 section 的组合快照。"""
        return FrozenContextCollectorRegistry(tuple(self._sections))


@dataclass(frozen=True)
class FrozenContextCollectorRegistry:
    """请求组装使用的不可变 section 集合。"""

    sections: tuple[ContextSection, ...] = ()

    def collect(self, input: ContextCollectionInput) -> list[ContextBlock]:
        """直接收集本次完整快照，不保留上次请求的投影。"""
        current_blocks: dict[str, list[ContextBlock]] = {}
        for section in self.sections:
            try:
                current_blocks[section.section_id] = section.collect(input)
            except (LookupError, OSError, RuntimeError, TypeError, ValueError):
                raise ContextAssemblyError(
                    f"Cannot render context section: {section.section_id}"
                ) from None
        return [block for blocks in current_blocks.values() for block in blocks]


def make_collector_section(
    section_id: str,
    collector: ContextCollector,
) -> ContextSection:
    """把 collector 包装为当前快照 section。"""

    return ContextSection(section_id, collector.collect)


def make_state_section(
    section_id: str,
    state_key: str,
    source: ContextBlockSource,
    *,
    priority: ContextPriority = ContextPriority.CONTEXT,
) -> ContextSection:
    """把 ContextCollectionInput.state 中的运行时快照包装为 section。"""

    def collect(input: ContextCollectionInput) -> list[ContextBlock]:
        value = input.state.get(state_key)
        if value is None:
            return []
        return [_state_block(section_id, source, priority, value)]

    return ContextSection(section_id, collect)


def _state_block(
    section_id: str,
    source: ContextBlockSource,
    priority: ContextPriority,
    value: object,
) -> ContextBlock:
    return ContextBlock(
        source=source,
        target=ContextBlockTarget.SYSTEM,
        priority=priority,
        content=(
            f'<context-section id="{section_id}" status="full">\n'
            f"{json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)}\n"
            "</context-section>"
        ),
        block_id=section_id,
        provenance=section_id,
        scope="runtime",
    )


# ── 组装器协议 ──


class ContextAssembler(Protocol):
    def assemble(self, input: ContextAssemblyInput) -> ContextAssemblyResult: ...


# ── 默认组装器 ──


class DefaultContextAssembler:
    def assemble(self, input: ContextAssemblyInput) -> ContextAssemblyResult:
        messages = list(input.messages)
        base_tokens = _estimate_base_tokens(input, messages)
        budget = input.token_budget

        # 这里只生成完整投影；请求边界回收历史后检查安全预算。
        used_blocks = sorted(input.context_blocks, key=lambda block: block.priority)

        trailing_user_blocks: list[ContextBlock] = []
        if used_blocks:
            system_blocks = [
                b for b in used_blocks if b.target == ContextBlockTarget.SYSTEM
            ]
            user_blocks = [
                b for b in used_blocks if b.target != ContextBlockTarget.SYSTEM
            ]
            prefix_user_blocks = [
                b
                for b in user_blocks
                if b.source == ContextBlockSource.SKILL
                or b.priority == ContextPriority.INSTRUCTION
            ]
            trailing_user_blocks = [
                b
                for b in user_blocks
                if b.source != ContextBlockSource.SKILL
                and b.priority != ContextPriority.INSTRUCTION
            ]

            insert_idx = 0
            for i, m in enumerate(messages):
                role = getattr(m, "role", "")
                if role == "system":
                    insert_idx = i + 1
                else:
                    break

            if system_blocks:
                system_messages = [
                    SystemMessage(content=b.content) for b in system_blocks
                ]
                messages[insert_idx:insert_idx] = system_messages
                insert_idx += len(system_messages)

            if prefix_user_blocks:
                prefix_user_messages = [
                    UserMessage(content=_block_to_text(b)) for b in prefix_user_blocks
                ]
                messages[insert_idx:insert_idx] = prefix_user_messages

        previous = next(
            (m for m in reversed(input.messages) if isinstance(m, ContextMessage)),
            None,
        )
        context_message = None
        if trailing_user_blocks or previous is not None:
            content = (
                "Current runtime context (complete snapshot; supersedes earlier "
                "runtime-context snapshots, which remain historical):\n\n"
                + "\n\n".join(_block_to_text(block) for block in trailing_user_blocks)
                if trailing_user_blocks
                else "Current runtime context: none. Earlier runtime-context "
                "snapshots no longer apply."
            )
            if previous is None or previous.content != content:
                context_message = ContextMessage(content=content)
                messages.append(context_message)

        final_total = _estimate_base_tokens(input, messages)

        return ContextAssemblyResult(
            messages=messages,
            context_message=context_message,
            blocks_used=used_blocks,
            total_tokens=final_total,
            token_budget=budget,
            budget_remaining=max(0, budget - final_total) if budget > 0 else 0,
            base_tokens=base_tokens,
        )


# ── 共享辅助函数 ──


def _block_to_text(block: ContextBlock) -> str:
    source_tag = f"[{block.source.value}]"
    return f"{source_tag}\n{block.content}"


def _estimate_messages_tokens(messages: list[AgentMessage]) -> int:
    return sum(
        estimate_wire_message_tokens(m, estimate_tokens)
        for m in convert_to_llm(messages)
    )


def _estimate_base_tokens(
    input: ContextAssemblyInput,
    messages: list[AgentMessage],
) -> int:
    """估算不含结构化上下文块的完整请求基线。"""
    total = _estimate_messages_tokens(messages)
    if input.system_prompt:
        total += estimate_tokens(input.system_prompt)
    total += _estimate_tools_tokens(input.tools)
    if input.calibrate_tokens is not None:
        return input.calibrate_tokens(messages, total)
    return total


def _estimate_tools_tokens(tools: list[AgentTool]) -> int:
    total = 0
    for tool in tools:
        description = str(getattr(tool, "description", ""))
        examples = getattr(tool, "examples", [])
        if examples:
            example_lines = ["\n", "Examples:"]
            for example in examples:
                example_lines.append(
                    f"  - {example.get('name', '')}: "
                    f"input={json.dumps(example.get('input', {}), ensure_ascii=False)}, "
                    f'output="{example.get("output", "")}"'
                )
            description += "\n".join(example_lines)
        payload = {
            "name": str(getattr(tool, "name", "")),
            "description": description,
            "parameters": materialize_json_mapping(getattr(tool, "parameters", {})),
        }
        builtin = getattr(tool, "builtin", None)
        if isinstance(builtin, dict):
            payload["builtin"] = builtin
        total += estimate_tokens(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        )
    return total


def _apply_size_budget(content: str, max_bytes: int, marker: str) -> str:
    if not content:
        return ""
    if _utf8_size(content) <= max_bytes:
        return content
    marker_bytes = _utf8_size(marker)
    budget = max_bytes - marker_bytes
    if budget <= 0:
        return ""
    return _utf8_prefix(content, budget) + marker


def _utf8_size(text: str) -> int:
    return len(text.encode("utf-8"))


def _utf8_prefix(text: str, max_bytes: int) -> str:
    data = text.encode("utf-8")
    if len(data) <= max_bytes:
        return text
    return data[:max_bytes].decode("utf-8", errors="ignore")


# ── 内置收集器：指令 ──

_INSTRUCTION_PRIORITY_MAP: dict[str, ContextPriority] = {
    "instruction": ContextPriority.INSTRUCTION,
    "context": ContextPriority.CONTEXT,
}


@dataclass
class InstructionSource:
    type: Literal["file", "inline"]
    path: str | None = None
    content: str | None = None
    priority: ContextPriority = ContextPriority.INSTRUCTION
    scope: Literal["user", "project"] = "user"


MANIFEST_MAX_BYTES: int = 32 * 1024


class InstructionCollector:
    def __init__(
        self,
        sources: tuple[dict, ...] = (),
        project_root: Path | None = None,
        cwd: Path | None = None,
    ) -> None:
        self._project_root = project_root
        self._cwd = cwd
        self._sources: list[InstructionSource] = []
        self._file_cache: dict[Path, tuple[tuple[int, int], bytes]] = {}
        self._discovery_cache: dict[
            tuple[Path, Path], tuple[tuple[tuple[Path, int], ...], tuple[Path, ...]]
        ] = {}
        for entry in sources:
            typ = entry["type"]
            priority_str = entry.get("priority", "instruction")
            priority = _INSTRUCTION_PRIORITY_MAP[priority_str.lower()]
            scope = entry.get("scope", "user")
            if scope not in {"user", "project"}:
                scope = "user"
            if typ == "file":
                self._sources.append(
                    InstructionSource(
                        type="file",
                        path=entry["path"],
                        priority=priority,
                        scope=scope,
                    )
                )
            else:
                self._sources.append(
                    InstructionSource(
                        type="inline",
                        content=entry["content"],
                        priority=priority,
                        scope=scope,
                    )
                )

    def collect(self, input: ContextCollectionInput) -> list[ContextBlock]:
        root_value = input.project_root or self._project_root
        root = root_value.resolve() if root_value is not None else None
        if root is None:
            return []
        cwd = self._normalise_cwd(input.cwd or self._cwd or root, root)

        blocks: list[ContextBlock] = []
        configured_paths: set[Path] = set()
        remaining_bytes = MANIFEST_MAX_BYTES

        for source in self._sources:
            if remaining_bytes <= 0:
                break
            if source.type == "file":
                if source.path is None:
                    raise RuntimeError("file instruction source has no path")
                path = root / source.path
                resolved = path.resolve()
                if resolved in configured_paths:
                    continue
                configured_paths.add(resolved)
                source_blocks, consumed_bytes = self._collect_file(
                    path, source.priority, remaining_bytes, source.scope
                )
            else:
                if source.content is None:
                    raise RuntimeError("inline instruction source has no content")
                source_blocks, consumed_bytes = self._collect_inline(
                    source.content, source.priority, remaining_bytes, source.scope
                )
            blocks.extend(source_blocks)
            remaining_bytes -= consumed_bytes

        for agents_path in self._discover_instruction_paths(root, cwd):
            if remaining_bytes <= 0:
                break
            resolved = agents_path.resolve()
            if resolved in configured_paths:
                continue
            source_blocks, consumed_bytes = self._collect_file(
                agents_path,
                ContextPriority.INSTRUCTION,
                remaining_bytes,
                "project",
            )
            blocks.extend(source_blocks)
            remaining_bytes -= consumed_bytes

        return blocks

    def _normalise_cwd(self, cwd: Path, root: Path) -> Path:
        resolved = cwd.resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            return root
        return resolved

    def _discover_instruction_paths(self, root: Path, cwd: Path) -> tuple[Path, ...]:
        directories = self._hierarchy_directories(root, cwd)
        signatures: list[tuple[Path, int]] = []
        for directory in directories:
            try:
                signatures.append((directory, directory.stat().st_mtime_ns))
            except OSError:
                signatures.append((directory, -1))
        cache_key = (root, cwd)
        cached = self._discovery_cache.get(cache_key)
        signature_tuple = tuple(signatures)
        if cached is not None and cached[0] == signature_tuple:
            return cached[1]

        paths: list[Path] = []
        for directory in directories:
            override = directory / "AGENTS.override.md"
            if override.is_file():
                paths.append(override)
                continue
            for filename in ("AGENTS.md", "agents.md", "AGENTS.txt"):
                candidate = directory / filename
                if candidate.is_file():
                    paths.append(candidate)
                    break
        result = tuple(paths)
        self._discovery_cache[cache_key] = (signature_tuple, result)
        return result

    @staticmethod
    def _hierarchy_directories(root: Path, cwd: Path) -> tuple[Path, ...]:
        try:
            relative = cwd.relative_to(root)
        except ValueError:
            relative = Path()
        directories = [root]
        current = root
        for part in relative.parts:
            current = current / part
            directories.append(current)
        return tuple(directories)

    def _read_cached(self, path: Path) -> bytes | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        signature = (stat.st_mtime_ns, stat.st_size)
        cached = self._file_cache.get(path)
        if cached is not None and cached[0] == signature:
            return cached[1]
        try:
            data = path.read_bytes()
        except OSError:
            return None
        self._file_cache[path] = (signature, data)
        return data

    def _collect_file(
        self,
        path: Path,
        priority: ContextPriority,
        max_bytes: int,
        scope: Literal["user", "project"],
    ) -> tuple[list[ContextBlock], int]:
        data = self._read_cached(path)
        if data is None:
            return [], 0
        content, consumed_bytes = _prepare_manifest_bytes(data, max_bytes, path)
        if not content:
            return [], consumed_bytes
        return [
            ContextBlock(
                source=ContextBlockSource.INSTRUCTION,
                target=ContextBlockTarget.SYSTEM,
                priority=priority,
                content=content,
                provenance=str(path),
                truncated=len(data) > max_bytes,
                truncation_reason="byte_budget" if len(data) > max_bytes else None,
                scope=scope,
            )
        ], consumed_bytes

    @staticmethod
    def _collect_inline(
        content: str,
        priority: ContextPriority,
        max_bytes: int,
        scope: Literal["user", "project"],
    ) -> tuple[list[ContextBlock], int]:
        data = content.encode("utf-8")
        prepared = _prepare_manifest(content, max_bytes, "inline instruction")
        consumed_bytes = min(len(data), max_bytes) if max_bytes > 0 else 0
        if not prepared:
            return [], consumed_bytes
        return [
            ContextBlock(
                source=ContextBlockSource.INSTRUCTION,
                target=ContextBlockTarget.SYSTEM,
                priority=priority,
                content=prepared,
                provenance="inline instruction",
                truncated=len(data) > max_bytes,
                truncation_reason="byte_budget" if len(data) > max_bytes else None,
                scope=scope,
            )
        ], consumed_bytes


def _prepare_manifest_bytes(
    data: bytes, max_bytes: int, source: Path | str
) -> tuple[str, int]:
    if max_bytes <= 0 or not data:
        return "", 0
    consumed_bytes = min(len(data), max_bytes)
    if len(data) > max_bytes:
        logger.warning(
            "project instruction exceeds remaining byte budget; truncating: %s",
            source,
        )
    return data[:consumed_bytes].decode("utf-8", errors="replace"), consumed_bytes


def _prepare_manifest(
    text: str,
    max_bytes: int = MANIFEST_MAX_BYTES,
    source: Path | str = "project instruction",
) -> str:
    prepared, _consumed_bytes = _prepare_manifest_bytes(
        text.encode("utf-8"), max_bytes, source
    )
    return prepared


# ── 内置收集器：笔记 ──


NOTES_MAX_BYTES: int = 4 * 1024
NOTES_MAX_FILE_BYTES: int = 64 * 1024
_NOTES_TRUNCATED_MARKER = (
    "<notes-truncated>NOTE.md truncated. Read the file for full content."
    "</notes-truncated>"
)


class NotesCollector:
    def __init__(
        self,
        project_root: Path | None = None,
        max_bytes: int = NOTES_MAX_BYTES,
    ) -> None:
        self._project_root = project_root
        self._max_bytes = max_bytes

    def collect(self, input: ContextCollectionInput) -> list[ContextBlock]:
        root = input.project_root or self._project_root
        if root is None:
            return []
        note_path = root / "NOTE.md"
        if not note_path.is_file():
            return []
        try:
            if note_path.stat().st_size > NOTES_MAX_FILE_BYTES:
                return []
            text = note_path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            logger.debug("NotesCollector: failed to read %s", note_path, exc_info=True)
            return []
        if not text:
            return []
        body = _apply_size_budget(text, self._max_bytes, _NOTES_TRUNCATED_MARKER)

        return [
            ContextBlock(
                source=ContextBlockSource.NOTES,
                target=ContextBlockTarget.USER_CONTEXT,
                priority=ContextPriority.CONTEXT,
                content=body,
                provenance=str(note_path),
                truncated=_NOTES_TRUNCATED_MARKER in body,
                truncation_reason=(
                    "byte_budget" if _NOTES_TRUNCATED_MARKER in body else None
                ),
                scope="runtime",
            )
        ]
