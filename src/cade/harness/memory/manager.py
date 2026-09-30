"""面向长任务的最小持久记忆。

MEMORY.md 是唯一事实源。检索只使用确定性的 BM25；会话连续性由
session surface 负责，不在长期记忆中维护反馈、效用或生命周期状态。
写入只有一条通道：显式 add/update 与经验写入工具共用同一套确定性校验。
"""

from __future__ import annotations

import os
import re
import tempfile
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

import filelock
from rank_bm25 import BM25Okapi

from .experience import (
    Experience,
    ExperienceStatus,
    anchor_freshness,
    experience_status,
    is_experience,
    missing_anchor_paths,
    parse_experience,
    render_hint_line,
    render_index_line,
)
from .experience import experience_problems as _experience_problems
from .parsing import MemoryRecord, parse_memory_blocks, tokenize

type MemoryLayer = Literal["project", "user"]
type MemoryLayerFilter = Literal["all", "project", "user"]
type MemoryFileSignature = tuple[str, int, int, int] | tuple[str, None, None, None]


@dataclass(frozen=True)
class _MemorySearchIndex:
    """可按文件签名安全失效的内存检索索引。"""

    signature: tuple[MemoryFileSignature, ...]
    records: tuple[MemoryRecord, ...]
    corpus: tuple[list[str], ...]
    index: BM25Okapi | None


class MemoryManager:
    """管理可审查的项目级与用户级 Markdown 记忆。"""

    def __init__(
        self,
        root: Path,
        *,
        user_memory_file: Path | None = None,
    ) -> None:
        self.root = root.resolve()
        self.memory_file = self.root / "MEMORY.md"
        self.user_memory_file = user_memory_file or (
            Path.home() / ".cade" / "memory" / "MEMORY.md"
        )
        self._search_indexes: dict[MemoryLayerFilter, _MemorySearchIndex] = {}
        self._search_indexes_lock = threading.Lock()

    def read_memory_blocks(
        self,
        layer: MemoryLayerFilter = "all",
    ) -> list[str]:
        """读取指定层级中的原始 H2 记忆块。"""
        return [record.block for record in self.read_memory_records(layer)]

    def read_memory_records(
        self,
        layer: MemoryLayerFilter = "all",
    ) -> list[MemoryRecord]:
        """读取指定层级的记忆记录。"""
        records: list[MemoryRecord] = []
        for current_layer in self._selected_layers(layer):
            path = self._memory_file(current_layer)
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            records.extend(parse_memory_blocks(text, layer=current_layer))
        return records

    def search_memory_records(
        self,
        query: str,
        *,
        limit: int = 5,
        layer: MemoryLayerFilter = "all",
        scope: str | None = None,
        anchor: str | None = None,
    ) -> list[MemoryRecord]:
        """使用 BM25 检索长期记忆。

        `scope` 仅作为附加检索词，不触发隐藏的重排策略。`anchor` 是仓库相对
        路径，只保留声明了该锚点的 Experience；它确定性地过滤结果，不参与打分。
        """
        normalized = query.strip()
        if not normalized and not anchor:
            return []
        if limit <= 0:
            return []
        search_index = self._search_index(layer)
        if not search_index.records or search_index.index is None:
            return []

        query_text = "\n".join(part for part in (normalized, scope or "") if part)
        query_tokens = tokenize(query_text)
        if not query_tokens and not anchor:
            return []
        raw_scores = (
            search_index.index.get_scores(query_tokens)
            if query_tokens
            else [0.0] * len(search_index.records)
        )
        lowered = normalized.casefold()

        ranked: list[MemoryRecord] = []
        for record, document_tokens, raw_score in zip(
            search_index.records,
            search_index.corpus,
            raw_scores,
            strict=True,
        ):
            if anchor is not None:
                experience = parse_experience(record)
                if experience is None or experience.matches_path(anchor) is None:
                    continue
            exact = bool(lowered) and lowered in record.search_text.casefold()
            overlap = len(set(query_tokens).intersection(document_tokens))
            if query_tokens and not exact and overlap == 0:
                continue
            score = (
                max(float(raw_score), 0.0) + float(overlap) + (1.0 if exact else 0.0)
            )
            ranked.append(replace(record, score=score))
        ranked.sort(
            key=lambda item: (
                -item.score,
                0 if item.layer == "project" else 1,
                item.title.casefold(),
            )
        )
        return ranked[: min(limit, 10)]

    def find_record(
        self,
        memory_id: str,
        *,
        layer: MemoryLayerFilter = "all",
    ) -> MemoryRecord | None:
        """按稳定 id 精确取一条记录，用于两段式读取的第二步。"""
        target = memory_id.strip()
        if not target:
            return None
        for record in self.read_memory_records(layer):
            if record.memory_id == target:
                return record
        return None

    def read_budgeted(
        self,
        max_tokens: int,
        layer: MemoryLayerFilter = "all",
    ) -> list[str]:
        """按文件顺序读取可装入预算的记忆，用于 resume/rebuild。"""
        if max_tokens <= 0:
            return []
        from cade.agent._context_window import estimate_tokens

        selected: list[str] = []
        remaining = max_tokens
        for record in self.read_memory_records(layer):
            packet = self.render_prompt_packet(record)
            cost = estimate_tokens(packet)
            if cost > remaining:
                continue
            selected.append(packet)
            remaining -= cost
        return selected

    def add_memory_block(
        self,
        block: str,
        *,
        layer: MemoryLayer = "project",
    ) -> bool:
        """显式追加一条记忆；拒绝空记录、标题重复和不合格的 Experience。"""
        incoming = self._parse_incoming_block(block, layer)
        if incoming is None:
            return False
        if memory_write_rejection(block, layer=layer, project_root=self.root):
            return False
        path = self._memory_file(layer)
        with self._file_lock(path):
            current = path.read_text(encoding="utf-8") if path.is_file() else ""
            existing = parse_memory_blocks(current, layer=layer)
            if self._duplicates(incoming, existing):
                return False
            prefix = current.rstrip()
            content = (
                f"{prefix}\n\n{incoming.block.strip()}\n"
                if prefix
                else (
                    f"# {'Project' if layer == 'project' else 'User'} memory\n\n"
                    f"{incoming.block.strip()}\n"
                )
            )
            self._atomic_write(path, content)
        self._invalidate_search_indexes()
        return True

    def update_memory_block(
        self,
        title: str,
        block: str,
        *,
        layer: MemoryLayer = "project",
    ) -> bool:
        """按标题原子替换一条记忆，并保留文件中的其他内容。"""
        incoming = self._parse_incoming_block(block, layer)
        target_title = title.strip().casefold()
        if incoming is None or not target_title:
            return False
        if memory_write_rejection(block, layer=layer, project_root=self.root):
            return False
        path = self._memory_file(layer)
        with self._file_lock(path):
            if not path.is_file():
                return False
            current = path.read_text(encoding="utf-8")
            spans = self._memory_block_spans(current)
            target = next(
                (span for span in spans if span[0].casefold() == target_title),
                None,
            )
            if target is None:
                return False
            existing = [
                record
                for record in parse_memory_blocks(current, layer=layer)
                if record.title.casefold() != target_title
            ]
            other_titles = {
                span_title.casefold()
                for span_title, _, _ in spans
                if span_title.casefold() != target_title
            }
            if incoming.title.casefold() in other_titles or self._duplicates(
                incoming, existing
            ):
                return False
            _, start, end = target
            suffix = current[end:].lstrip("\r\n")
            content = current[:start].rstrip()
            if content:
                content += "\n\n"
            content += incoming.block.strip()
            content += f"\n\n{suffix}" if suffix else "\n"
            self._atomic_write(path, content)
        self._invalidate_search_indexes()
        return True

    def delete_memory_block(
        self,
        title: str,
        *,
        layer: MemoryLayer = "project",
    ) -> bool:
        """按标题原子删除一条记忆，并保留文件头和其他记录。"""
        target_title = title.strip().casefold()
        if not target_title:
            return False
        path = self._memory_file(layer)
        with self._file_lock(path):
            if not path.is_file():
                return False
            current = path.read_text(encoding="utf-8")
            target = next(
                (
                    span
                    for span in self._memory_block_spans(current)
                    if span[0].casefold() == target_title
                ),
                None,
            )
            if target is None:
                return False
            _, start, end = target
            prefix = current[:start].rstrip()
            suffix = current[end:].lstrip("\r\n")
            if prefix and suffix:
                content = f"{prefix}\n\n{suffix}"
            elif prefix:
                content = f"{prefix}\n"
            else:
                content = suffix
            self._atomic_write(path, content)
        self._invalidate_search_indexes()
        return True

    def render_prompt_packet(self, record: MemoryRecord) -> str:
        """恢复会话时的记忆包：坏记录只剩修复线索，合法经验只指针。"""
        status = experience_status(record)
        if status.tier == "invalid":
            return (
                f"[{record.layer} memory · {record.memory_id} · INVALID experience]\n"
                f"{'; '.join(status.problems)}\n"
                "Fix MEMORY.md before relying on this record."
            )
        experience = parse_experience(record)
        if experience is not None:
            return self.render_hint_packet(experience)
        return f"[{record.layer} memory · {record.memory_id}]\n{record.block.strip()}"

    def render_hint_packet(self, experience: Experience) -> str:
        """渲染一行 Experience 指针，供概览、hint 与工具输出复用。"""
        freshness = anchor_freshness(experience, self.root)
        return (
            f"[{experience.layer} experience]\n"
            f"{render_hint_line(experience, freshness)}"
        )

    def render_index_result(self, record: MemoryRecord) -> str:
        """检索结果的第一段：只给指针，正文需要按 memory_id 再取一次。"""
        status = experience_status(record)
        if status.tier == "invalid":
            return render_index_line(record, status)
        experience = parse_experience(record)
        if experience is None:
            return (
                f"[{record.layer}] {record.memory_id} | {record.title} "
                f"| memory (not an experience)"
            )
        freshness = anchor_freshness(experience, self.root)
        return render_index_line(record, status, freshness)

    def render_full_result(self, record: MemoryRecord) -> str:
        """检索结果的第二段：按 id 精确取回正文；坏记录拒绝交付正文。"""
        status: ExperienceStatus = experience_status(record)
        if status.tier == "invalid":
            return (
                f"[{record.layer}] {record.memory_id} | INVALID experience\n"
                f"{'; '.join(status.problems)}\n"
                "The record is not consumed until MEMORY.md is repaired."
            )
        header = f"[{record.layer}] {record.title} (memory_id={record.memory_id}"
        experience = parse_experience(record)
        if experience is not None:
            evidence = experience.evidence_value("validation") or "none"
            header += (
                f", evidence={experience.evidence_tier}, validation={evidence}"
                f", state={anchor_freshness(experience, self.root)}"
            )
        return f"{header})\n{record.block.strip()}"

    def _memory_file(self, layer: MemoryLayer | str) -> Path:
        if layer not in {"project", "user"}:
            raise ValueError(f"unsupported memory layer: {layer}")
        return self.memory_file if layer == "project" else self.user_memory_file

    @staticmethod
    def _selected_layers(layer: MemoryLayerFilter) -> tuple[MemoryLayer, ...]:
        if layer == "project":
            return ("project",)
        if layer == "user":
            return ("user",)
        return ("project", "user")

    def _search_index(self, layer: MemoryLayerFilter) -> _MemorySearchIndex:
        signature = self._memory_signature(layer)
        with self._search_indexes_lock:
            cached = self._search_indexes.get(layer)
            if cached is not None and cached.signature == signature:
                return cached

        stable_signature: tuple[MemoryFileSignature, ...] = ()
        records: tuple[MemoryRecord, ...] = ()
        for _ in range(3):
            signature_before = self._memory_signature(layer)
            records = tuple(self.read_memory_records(layer))
            signature_after = self._memory_signature(layer)
            if signature_before == signature_after:
                stable_signature = signature_after
                break

        corpus = tuple(tokenize(record.search_text) for record in records)
        index = BM25Okapi(list(corpus)) if corpus else None
        rebuilt = _MemorySearchIndex(
            signature=stable_signature,
            records=records,
            corpus=corpus,
            index=index,
        )
        with self._search_indexes_lock:
            self._search_indexes[layer] = rebuilt
        return rebuilt

    def _memory_signature(
        self,
        layer: MemoryLayerFilter,
    ) -> tuple[MemoryFileSignature, ...]:
        signatures: list[MemoryFileSignature] = []
        for current_layer in self._selected_layers(layer):
            path = self._memory_file(current_layer)
            try:
                stat = path.stat()
            except FileNotFoundError:
                signatures.append((str(path), None, None, None))
                continue
            signatures.append((str(path), stat.st_ino, stat.st_mtime_ns, stat.st_size))
        return tuple(signatures)

    def _invalidate_search_indexes(self) -> None:
        with self._search_indexes_lock:
            self._search_indexes.clear()

    @staticmethod
    def _file_lock(path: Path) -> filelock.FileLock:
        path.parent.mkdir(parents=True, exist_ok=True)
        return filelock.FileLock(f"{path}.lock", timeout=10)

    @staticmethod
    def _parse_incoming_block(
        block: str,
        layer: MemoryLayer,
    ) -> MemoryRecord | None:
        parsed = parse_memory_blocks(block, layer=layer)
        if len(parsed) != 1 or len(parsed[0].body.strip()) < 3:
            return None
        return parsed[0]

    @staticmethod
    def _duplicates(
        incoming: MemoryRecord,
        existing: list[MemoryRecord],
    ) -> bool:
        return any(
            record.title.casefold() == incoming.title.casefold()
            or record.body.casefold() == incoming.body.casefold()
            for record in existing
        )

    @staticmethod
    def _memory_block_spans(text: str) -> list[tuple[str, int, int]]:
        matches = list(re.finditer(r"(?m)^##[ \t]+(.+?)[ \t]*$", text))
        return [
            (
                match.group(1).strip(),
                match.start(),
                matches[index + 1].start() if index + 1 < len(matches) else len(text),
            )
            for index, match in enumerate(matches)
        ]

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
        replaced = False
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            replaced = True
        finally:
            if not replaced:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass


def build_memory_block(title: str, body: str) -> str:
    """构建供 CLI 和调用方使用的最小 Markdown 记忆块。"""
    clean_title = re.sub(r"[\r\n]+", " ", title).strip()
    clean_body = body.strip()
    return f"## {clean_title}\n{clean_body}\n"


def memory_write_rejection(
    block: str,
    *,
    layer: MemoryLayer,
    project_root: Path,
) -> str | None:
    """校验一条待写入的记忆块；返回拒绝原因，None 表示可以写入。

    普通规则只做结构检查。Experience 额外要求根因、修复、适用条件、至少一个
    真实存在的路径锚点（若声明了路径锚点）和至少一个溯源指针；它只允许写入
    项目层，因为仓库相对锚点在别的项目里会指向完全不同的文件。
    """
    records = parse_memory_blocks(block, layer=layer)
    if len(records) != 1 or len(records[0].body.strip()) < 3:
        return "record must be exactly one non-empty H2 section"
    record = records[0]
    if not is_experience(record):
        return None
    if layer != "project":
        return "experience records are project-scoped; use user memory for preferences"
    problems = _experience_problems(record)
    if problems:
        return "experience is incomplete: " + "; ".join(problems)
    experience = parse_experience(record)
    if experience is None:
        return "experience is incomplete"
    missing = missing_anchor_paths(experience, project_root)
    if missing:
        return "anchor path not found in repository: " + ", ".join(missing)
    return None
