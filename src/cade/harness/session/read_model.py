"""会话日志的可丢弃增量索引与一致性读取视图。"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType

from .types import SessionEntry

Decoder = Callable[[bytes], SessionEntry]


def select_branch_ids(
    parents: Mapping[str, str | None], head_id: str | None
) -> tuple[str, ...]:
    """按提交的 head 回溯分支，拒绝缺失节点和循环。"""
    branch: list[str] = []
    seen: set[str] = set()
    current = head_id
    while current is not None:
        if current in seen or current not in parents:
            raise ValueError(f"invalid session branch at entry: {current}")
        seen.add(current)
        branch.append(current)
        current = parents[current]
    return tuple(reversed(branch))


@dataclass(frozen=True)
class SessionRevision:
    """标识文件实例、已读取的完整记录前缀与当前分支。"""

    file_identity: tuple[int, int] | None
    committed_bytes: int
    head_id: str | None


@dataclass(frozen=True)
class _IndexedEntry:
    entry: SessionEntry
    event_type: str | None
    offset: int
    length: int
    digest: bytes

    @property
    def is_audit(self) -> bool:
        return self.event_type == "provider_request"


@dataclass(frozen=True)
class SessionReadView:
    """固定提交前缀上的只读视图；精确审计正文只按需解码。"""

    revision: SessionRevision
    branch_ids: tuple[str, ...]
    _path: Path
    _nodes: Mapping[str, _IndexedEntry]
    _decode: Decoder

    @property
    def entry_ids(self) -> tuple[str, ...]:
        return tuple(self._nodes)

    def facts(self, *event_types: str) -> list[SessionEntry]:
        """返回完整语义事实，不将审计占位符伪装成原始记录。"""
        selected = frozenset(event_types)
        return [
            deepcopy(node.entry)
            for entry_id in self.branch_ids
            if not (node := self._nodes[entry_id]).is_audit
            and (not selected or node.event_type in selected)
        ]

    def branch(self) -> list[SessionEntry]:
        """精确读取当前分支，包含完整审计。"""
        return [self._exact(self._nodes[entry_id]) for entry_id in self.branch_ids]

    def entries(self) -> list[SessionEntry]:
        """精确读取全部分支记录，保持文件顺序。"""
        return [self._exact(node) for node in self._nodes.values()]

    def get(self, entry_id: str) -> SessionEntry | None:
        """按稳定 ID 精确读取记录，不限制在当前分支。"""
        node = self._nodes.get(entry_id)
        return self._exact(node) if node is not None else None

    def _exact(self, node: _IndexedEntry) -> SessionEntry:
        if not node.is_audit:
            return deepcopy(node.entry)
        with self._path.open("rb") as stream:
            stat = os.fstat(stream.fileno())
            if (stat.st_dev, stat.st_ino) != self.revision.file_identity:
                raise ValueError("Session file changed since the read view was created")
            stream.seek(node.offset)
            payload = stream.read(node.length)
        if hashlib.sha256(payload).digest() != node.digest:
            raise ValueError("Session record changed since the read view was created")
        return self._decode(payload)


class SessionReadIndex:
    """在存储锁内同步提交后缀；不持有历史审计正文或独立可写状态。"""

    def __init__(self, path: Path, decode: Decoder) -> None:
        self.path = path
        self._decode = decode
        self._nodes: dict[str, _IndexedEntry] = {}
        self._identity: tuple[int, int] | None = None
        self._offset = 0
        self._stamp: tuple[int, int] | None = None
        self._last_id: str | None = None

    def view(self, legacy_head_id: str | None = None) -> SessionReadView:
        """调用方持有 SessionStore 的文件锁；失败时丢弃不完整索引。"""
        try:
            self._sync()
            last = self._nodes[self._last_id].entry if self._last_id else None
            head = (last.head_id or legacy_head_id or last.id) if last else None
            nodes = dict(self._nodes)
            branch = select_branch_ids(
                {entry_id: node.entry.parent_id for entry_id, node in nodes.items()},
                head,
            )
            return SessionReadView(
                SessionRevision(self._identity, self._offset, head),
                branch,
                self.path,
                MappingProxyType(nodes),
                self._decode,
            )
        except (OSError, TypeError, ValueError):
            self._reset()
            raise

    def needs_legacy_head(self) -> bool:
        """同步后判断是否需要旧导航索引中的 head。"""
        return bool(self._last_id and self._nodes[self._last_id].entry.head_id is None)

    def _reset(self) -> None:
        self._nodes.clear()
        self._identity = None
        self._offset = 0
        self._stamp = None
        self._last_id = None

    def _sync(self) -> None:
        try:
            stream = self.path.open("rb")
        except FileNotFoundError:
            self._reset()
            return
        with stream:
            stat = os.fstat(stream.fileno())
            identity = (stat.st_dev, stat.st_ino)
            stamp = (stat.st_size, stat.st_mtime_ns)
            if identity != self._identity or stat.st_size < self._offset:
                self._reset()
            elif self._stamp == stamp:
                return
            elif self._stamp and stat.st_size == self._stamp[0]:
                # 支持已知替换和等长改写；普通追加不重读前缀。
                self._reset()
            self._identity = identity
            stream.seek(self._offset)
            while line := stream.readline():
                start = stream.tell() - len(line)
                if not line.strip():
                    self._offset = stream.tell()
                    continue
                try:
                    entry = self._decode(line)
                except ValueError as exc:
                    if not line.endswith(b"\n"):
                        break
                    raise ValueError(
                        f"invalid session record at byte {start}: {self.path}"
                    ) from exc
                if entry.id in self._nodes:
                    raise ValueError(f"duplicate session entry ID: {entry.id}")
                if entry.parent_id is not None and entry.parent_id not in self._nodes:
                    raise ValueError(
                        f"missing or forward session parent: {entry.parent_id}"
                    )
                if (
                    entry.head_id is not None
                    and entry.head_id not in self._nodes
                    and entry.head_id != entry.id
                ):
                    raise ValueError(f"missing session head: {entry.head_id}")
                content = entry.content
                event_type = (
                    content.get("type")
                    if entry.type == "event" and isinstance(content, dict)
                    else None
                )
                event_type = event_type if isinstance(event_type, str) else None
                stored = (
                    replace(entry, content=None)
                    if event_type == "provider_request"
                    else entry
                )
                self._nodes[entry.id] = _IndexedEntry(
                    stored, event_type, start, len(line), hashlib.sha256(line).digest()
                )
                self._last_id = entry.id
                self._offset = stream.tell()
            self._stamp = stamp
