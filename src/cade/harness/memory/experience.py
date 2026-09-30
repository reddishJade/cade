"""Experience 视图：把一条 Markdown 记录解释为可复用的排障经验。

一条 Experience 表达"过去这里踩过这个坑：根因是 X，当时 Y 有效，在这些条件
仍成立时值得优先验证"，而不是"当前代码一定还是这样"。仓库、文件与测试始终是
当前事实；Experience 只是历史提示。

本模块不保存任何 per-record 治理状态。新鲜度来自写入时记录的**内容快照**
（`anchor_state`），读取时对同样路径重新计算：它不依赖 git commit，因此在
"改完还没提交"的真实工作流里仍然准确。
"""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .parsing import MemoryRecord, field_lines

EXPERIENCE_TYPE = "experience"

# 旧版记忆的治理字段：携带这些字段的 Experience 会被拒绝写入，
# 以免重新长出置信度、效用或生命周期状态机。
RETIRED_GOVERNANCE_KEYS = frozenset(
    {
        "confidence",
        "status",
        "validity",
        "utility",
        "last-outcome",
        "supersedes",
        "retrieval-count",
        "injection-count",
        "reference-count",
        "adoption-count",
        "success-count",
        "failure-count",
        "correction-count",
    }
)

type AnchorKind = Literal["file", "dir", "symbol", "error"]
type Freshness = Literal["unchanged", "changed", "missing", "unknown"]
type EvidenceTier = Literal["event", "claim"]
type StatusTier = Literal["not_experience", "invalid", "event", "claim"]

_ANCHOR_PREFIXES: dict[str, AnchorKind] = {
    "file": "file",
    "dir": "dir",
    "sym": "symbol",
    "symbol": "symbol",
    "err": "error",
    "error": "error",
}
_ANCHOR_LABELS: dict[AnchorKind, str] = {
    "file": "file",
    "dir": "dir",
    "symbol": "sym",
    "error": "err",
}
_PATH_ANCHOR_KINDS: frozenset[AnchorKind] = frozenset({"file", "dir"})
_REQUIRED_FIELDS = ("root_cause", "fix", "applies_when")
_SNAPSHOT_ALGORITHM = "sha256"
_GIT_TIMEOUT_SECONDS = 5.0
_HINT_APPLIES_LIMIT = 80
_HINT_ANCHORS_LIMIT = 3


@dataclass(frozen=True)
class Anchor:
    """Experience 的定位锚点。"""

    kind: AnchorKind
    value: str

    @property
    def auto_hint(self) -> bool:
        """哪些锚点能确定性地触发自动提示；符号锚点只支持显式召回。"""
        return self.kind in _PATH_ANCHOR_KINDS or self.kind == "error"


@dataclass(frozen=True)
class ValidationEvidence:
    """一次真实成功验证事件的事实，由 Host 从 session 账本读出。"""

    message_id: str
    command: str
    exit_code: int
    file_state: str


@dataclass(frozen=True)
class Experience:
    """一条通过结构校验的排障经验；事实源仍是它所属的 Markdown 记录。"""

    root_cause: str
    fix: str
    applies_when: str
    anchors: tuple[Anchor, ...]
    evidence: tuple[str, ...]
    record: MemoryRecord

    @property
    def problem(self) -> str:
        """问题陈述就是记录标题，避免标题与字段两处漂移。"""
        return self.record.title

    @property
    def memory_id(self) -> str:
        return self.record.memory_id

    @property
    def layer(self) -> str:
        return self.record.layer

    @property
    def path_anchors(self) -> tuple[str, ...]:
        return tuple(
            anchor.value for anchor in self.anchors if anchor.kind in _PATH_ANCHOR_KINDS
        )

    @property
    def file_anchors(self) -> tuple[str, ...]:
        return tuple(anchor.value for anchor in self.anchors if anchor.kind == "file")

    @property
    def error_anchors(self) -> tuple[str, ...]:
        return tuple(anchor.value for anchor in self.anchors if anchor.kind == "error")

    @property
    def auto_hint_anchors(self) -> tuple[Anchor, ...]:
        return tuple(anchor for anchor in self.anchors if anchor.auto_hint)

    @property
    def evidence_tier(self) -> EvidenceTier:
        """区分为"有真实事件指针"与"只有主张"，读取方据此决定信任程度。"""
        if self.evidence_value("validation") and self.evidence_value("session"):
            return "event"
        return "claim"

    def evidence_value(self, key: str) -> str | None:
        prefix = f"{key.casefold()}="
        for item in self.evidence:
            if item.casefold().startswith(prefix):
                return item[len(prefix) :].strip() or None
        return None

    def matches_path(self, path: str) -> Anchor | None:
        """返回命中的路径锚点；文件精确匹配优先于目录前缀匹配。"""
        normalized = normalize_repo_path(path)
        if not normalized:
            return None
        directory_match: Anchor | None = None
        for anchor in self.anchors:
            if anchor.kind == "file" and normalized == anchor.value:
                return anchor
            if anchor.kind == "dir" and normalized.startswith(f"{anchor.value}/"):
                directory_match = directory_match or anchor
        return directory_match

    def matches_error(self, text: str) -> Anchor | None:
        """错误锚点必须在文本中原样出现（大小写不敏感），不做语义推断。"""
        lowered = text.casefold()
        for anchor in self.anchors:
            if anchor.kind == "error" and anchor.value.casefold() in lowered:
                return anchor
        return None


@dataclass(frozen=True)
class ExperienceStatus:
    """一条记录的可消费程度：不是经验 / 结构不合法 / 有事件证据 / 只是主张。"""

    tier: StatusTier
    problems: tuple[str, ...] = ()

    @property
    def valid(self) -> bool:
        return self.tier != "invalid"


def experience_status(record: MemoryRecord) -> ExperienceStatus:
    """判定记录能否作为经验被消费；结构不合法的记录只以索引形式出现。"""
    if not is_experience(record):
        return ExperienceStatus("not_experience")
    problems = experience_problems(record)
    if problems:
        return ExperienceStatus("invalid", problems)
    experience = parse_experience(record)
    if experience is None:  # pragma: no cover - 与上面的校验共享同一判据
        return ExperienceStatus("invalid", ("experience could not be parsed",))
    return ExperienceStatus(experience.evidence_tier)


def is_experience(record: MemoryRecord) -> bool:
    return field_lines(record.body).get("type", "").casefold() == EXPERIENCE_TYPE


def experience_problems(record: MemoryRecord) -> tuple[str, ...]:
    """返回不满足 Experience 约定的具体原因；空 tuple 表示合法。"""
    fields = field_lines(record.body)
    if fields.get("type", "").casefold() != EXPERIENCE_TYPE:
        return (f"type must be {EXPERIENCE_TYPE}",)
    problems = [
        f"{name} is required" for name in _REQUIRED_FIELDS if not fields.get(name)
    ]
    governance = sorted(RETIRED_GOVERNANCE_KEYS.intersection(field_lines(record.block)))
    if governance:
        problems.append(
            "retired governance fields are not allowed: " + ", ".join(governance)
        )
    if not parse_anchors(fields.get("anchors", "")):
        problems.append("anchors must list at least one anchor")
    if not parse_evidence(fields.get("evidence", "")):
        problems.append("evidence must record at least one pointer")
    return tuple(problems)


def parse_experience(record: MemoryRecord) -> Experience | None:
    """解释一条记忆记录；不是 Experience 或不合约定时返回 None。"""
    if experience_problems(record):
        return None
    fields = field_lines(record.body)
    return Experience(
        root_cause=fields["root_cause"],
        fix=fields["fix"],
        applies_when=fields["applies_when"],
        anchors=parse_anchors(fields["anchors"]),
        evidence=parse_evidence(fields["evidence"]),
        record=record,
    )


def parse_anchors(raw: str) -> tuple[Anchor, ...]:
    """解析逗号分隔的锚点；裸路径视为文件，目录需显式写 `dir=`。"""
    anchors: list[Anchor] = []
    for item in raw.split(","):
        text = item.strip()
        if not text:
            continue
        prefix, separator, rest = text.partition("=")
        kind = _ANCHOR_PREFIXES.get(prefix.strip().casefold()) if separator else None
        if kind is None:
            kind, value = "file", text
        else:
            value = rest.strip()
        if not value:
            continue
        if kind in _PATH_ANCHOR_KINDS:
            value = normalize_repo_path(value)
            if not value:
                continue
        anchors.append(Anchor(kind=kind, value=value))
    return tuple(anchors)


def parse_evidence(raw: str) -> tuple[str, ...]:
    """解析 `;` 分隔的溯源指针，例如 `session=9f2c1a7b; validation=ab12cd34ef56`。"""
    return tuple(item.strip() for item in raw.split(";") if item.strip())


def normalize_repo_path(value: str) -> str:
    """规范化为仓库相对 POSIX 路径；绝对路径与上跳路径返回空串。"""
    text = value.strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    text = text.rstrip("/")
    if not text or ".." in text.split("/"):
        return ""
    head = text.split("/")[0]
    if not head or ":" in head:
        return ""
    return text


def missing_anchor_paths(experience: Experience, project_root: Path) -> tuple[str, ...]:
    """写入前校验：路径锚点必须在当前仓库真实存在，防止幻觉锚点。"""
    missing: list[str] = []
    for anchor in experience.anchors:
        if anchor.kind not in _PATH_ANCHOR_KINDS:
            continue
        target = project_root / anchor.value
        exists = target.is_file() if anchor.kind == "file" else target.is_dir()
        if not exists:
            missing.append(anchor.value)
    return tuple(missing)


def anchor_snapshot(file_anchors: tuple[str, ...], project_root: Path) -> str | None:
    """对文件锚点的实际内容取快照；没有文件锚点或读不到时返回 None。

    快照只覆盖**文件**锚点：目录锚点仍可触发提示，但新鲜度记 unknown，
    避免为了一个目录去递归 hash 整棵子树。
    """
    if not file_anchors:
        return None
    digest = hashlib.new(_SNAPSHOT_ALGORITHM)
    for relative in sorted(set(file_anchors)):
        path = project_root / relative
        digest.update(relative.encode("utf-8") + b"\0")
        try:
            digest.update(path.read_bytes())
        except OSError:
            return None
        digest.update(b"\0")
    return f"{_SNAPSHOT_ALGORITHM}:{digest.hexdigest()}"


def parse_snapshot(value: str | None) -> str | None:
    """校验 `sha256:<hex>` 形态；不符返回 None。"""
    if not value or ":" not in value:
        return None
    algorithm, _, digest = value.partition(":")
    if algorithm != _SNAPSHOT_ALGORITHM or not digest:
        return None
    return value


def anchor_freshness(experience: Experience, project_root: Path) -> Freshness:
    """按内容快照对比锚点是否变化；不看 git commit，也不看分支状态。

    `unchanged` 只表示锚点文件内容与写入时一致，不表示结论仍然成立。
    """
    recorded = parse_snapshot(experience.evidence_value("anchor_state"))
    file_anchors = experience.file_anchors
    if recorded is None or not file_anchors:
        return "unknown"
    for relative in file_anchors:
        if not (project_root / relative).is_file():
            return "missing"
    current = anchor_snapshot(file_anchors, project_root)
    if current is None:
        return "missing"
    return "unchanged" if current == recorded else "changed"


def git_head(project_root: Path) -> str | None:
    """返回当前 HEAD 短 sha；非 Git 仓库或不可用时返回 None。

    它只作为附加证据，不参与新鲜度判定。
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=project_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    revision = result.stdout.strip()
    return revision if result.returncode == 0 and revision else None


def render_anchor(anchor: Anchor) -> str:
    """渲染锚点；路径锚点保持可直接使用的相对路径。"""
    if anchor.kind in _PATH_ANCHOR_KINDS:
        return anchor.value
    return f"{_ANCHOR_LABELS[anchor.kind]}={anchor.value}"


def render_hint_line(
    experience: Experience,
    freshness: Freshness = "unknown",
) -> str:
    """渲染单行指针：只给定位、取证与新鲜度，绝不复制经验正文。"""
    anchors = ", ".join(
        render_anchor(anchor) for anchor in experience.anchors[:_HINT_ANCHORS_LIMIT]
    )
    return (
        f"- {_summarize(experience.problem, _HINT_APPLIES_LIMIT)}"
        f" | applies_when: {_summarize(experience.applies_when, _HINT_APPLIES_LIMIT)}"
        f" | anchors: {anchors}"
        f" | evidence={experience.evidence_tier}"
        f" | state={freshness}"
    )


def render_index_line(
    record: MemoryRecord,
    status: ExperienceStatus,
    freshness: Freshness = "unknown",
) -> str:
    """渲染检索索引行：合法经验给指针，坏记录只给修复线索。"""
    if status.tier == "invalid":
        reasons = "; ".join(status.problems)
        return f"[{record.layer}] {record.memory_id} | INVALID experience | {reasons}"
    experience = parse_experience(record)
    if experience is None:  # pragma: no cover - status 已判定为合法
        return f"[{record.layer}] {record.memory_id} | {record.title}"
    return (
        f"[{experience.layer}] {experience.memory_id}"
        f"{render_hint_line(experience, freshness)[1:]}"
    )


def _summarize(value: str, limit: int) -> str:
    text = " ".join(value.split())
    return text if len(text) <= limit else f"{text[: limit - 1]}…"
