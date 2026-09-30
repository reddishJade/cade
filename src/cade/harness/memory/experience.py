"""Experience 视图：把一条 Markdown 记录解释为可复用的排障经验。

一条 Experience 表达的是"过去这里踩过这个坑：根因是 X，当时 Y 有效，
在这些条件仍成立时值得优先验证"，而不是"当前代码一定还是这样"。
仓库、文件与 git 始终是当前事实的来源；Experience 只是历史提示。

本模块不保存任何 per-record 治理状态：没有置信度、效用、计数器或生命周期。
新鲜度由 git 在读取时现算，不落盘。
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .parsing import MemoryRecord, field_lines

EXPERIENCE_TYPE = "experience"

# 旧版记忆的治理字段。Experience 记录携带这些字段会被拒绝写入，
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
type Freshness = Literal["unchanged", "changed", "unknown"]

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
_GIT_TIMEOUT_SECONDS = 5.0
_HINT_APPLIES_LIMIT = 80
_HINT_EVIDENCE_LIMIT = 120
_HINT_ANCHORS_LIMIT = 3


@dataclass(frozen=True)
class Anchor:
    """Experience 的定位锚点：文件、目录、符号或错误签名。"""

    kind: AnchorKind
    value: str


@dataclass(frozen=True)
class Experience:
    """一条经过结构校验的排障经验，事实源仍是它所属的 Markdown 记录。"""

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
    def error_anchors(self) -> tuple[str, ...]:
        return tuple(anchor.value for anchor in self.anchors if anchor.kind == "error")

    def evidence_value(self, key: str) -> str | None:
        prefix = f"{key.casefold()}="
        for item in self.evidence:
            if item.casefold().startswith(prefix):
                return item[len(prefix) :].strip() or None
        return None

    def matches_path(self, path: str) -> str | None:
        """返回命中的路径锚点；文件精确匹配优先于目录前缀匹配。"""
        normalized = normalize_repo_path(path)
        if not normalized:
            return None
        directory_match: str | None = None
        for anchor in self.anchors:
            if anchor.kind == "file" and normalized == anchor.value:
                return anchor.value
            if anchor.kind == "dir" and normalized.startswith(f"{anchor.value}/"):
                directory_match = directory_match or anchor.value
        return directory_match

    def matches_error(self, text: str) -> str | None:
        """错误锚点必须在文本中原样出现（大小写不敏感），不做语义推断。"""
        lowered = text.casefold()
        for value in self.error_anchors:
            if value.casefold() in lowered:
                return value
        return None


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
    anchors = parse_anchors(fields.get("anchors", ""))
    if not any(anchor.kind in _PATH_ANCHOR_KINDS for anchor in anchors):
        problems.append("anchors must include at least one file or dir anchor")
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
    """解析 `;` 分隔的溯源指针，例如 `commit=1a2b3c4; session=9f2c1a7b`。"""
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


def git_head(project_root: Path) -> str | None:
    """返回当前 HEAD 短 sha；非 Git 仓库或不可用时返回 None。"""
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


def anchor_freshness(experience: Experience, project_root: Path) -> Freshness:
    """用 git 现算锚点相对记录 commit 是否变化；未知一律返回 unknown。

    `unchanged` 只表示锚点文件与该 commit 一致，不表示结论仍然成立。
    """
    commit = experience.evidence_value("commit")
    paths = experience.path_anchors
    if not commit or not paths:
        return "unknown"
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", commit, "--", *paths],
            cwd=project_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    if result.returncode:
        return "unknown"
    return "changed" if result.stdout.strip() else "unchanged"


def render_hint_line(experience: Experience, freshness: Freshness) -> str:
    """渲染单行指针：只给定位与取证信息，绝不复制经验正文。"""
    anchors = ", ".join(
        render_anchor(anchor) for anchor in experience.anchors[:_HINT_ANCHORS_LIMIT]
    )
    evidence = " ".join(experience.evidence)
    return (
        f"- {_summarize(experience.problem, _HINT_APPLIES_LIMIT)}"
        f" | applies_when: {_summarize(experience.applies_when, _HINT_APPLIES_LIMIT)}"
        f" | anchors: {anchors}"
        f" | evidence: {_summarize(evidence, _HINT_EVIDENCE_LIMIT)}"
        f" | state={freshness}"
    )


def render_anchor(anchor: Anchor) -> str:
    """渲染锚点；路径锚点保持可直接使用的相对路径。"""
    if anchor.kind in _PATH_ANCHOR_KINDS:
        return anchor.value
    return f"{_ANCHOR_LABELS[anchor.kind]}={anchor.value}"


def _summarize(value: str, limit: int) -> str:
    text = " ".join(value.split())
    return text if len(text) <= limit else f"{text[: limit - 1]}…"
