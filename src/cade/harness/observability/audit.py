from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

"""工具执行审计与敏感信息脱敏。"""


SECRET_PATTERNS = (
    re.compile(r"sk-[^\s]+", re.IGNORECASE),
    re.compile(
        r"(?P<prefix>\b(api[_-]?key|secret|token|password)\b\s*[:=]\s*[\"']?)[A-Za-z0-9_./+=-]{8,}",
        re.IGNORECASE,
    ),
    re.compile(r"(?P<prefix>Bearer\s+)[^\s]+", re.IGNORECASE),
    re.compile(r"(?P<prefix>(?:api_key|token|secret)=)[^\s]+", re.IGNORECASE),
)


@dataclass(frozen=True)
class AuditRecord:
    session_id: str
    tool: str
    dynamic_decision: str
    policy_decision: str | None
    final_status: str
    approved: bool
    redacted_input: str
    redacted_output: str
    timestamp: str = ""
    turn_id: str = ""
    request_id: str = ""
    tool_call_id: str = ""
    approval_scope: str | None = None
    approval_decision: str | None = None
    capability: str | None = None
    target_kind: str | None = None
    target_value: str | None = None
    matched_rule: str | None = None
    approval_source: str | None = None
    approval_reviewer: str | None = None
    approval_review_status: str | None = None
    approval_rationale: str | None = None
    approval_risk: str | None = None
    approval_authorization: str | None = None
    approval_grant_id: str | None = None

    def to_dict(self, timestamp: str | None = None) -> dict[str, str | bool | None]:
        created_at = self.timestamp or timestamp or datetime.now(UTC).isoformat()
        return {
            "session_id": self.session_id,
            "tool": self.tool,
            "dynamic_decision": self.dynamic_decision,
            "policy_decision": self.policy_decision,
            "final_status": self.final_status,
            "approved": self.approved,
            "redacted_input": self.redacted_input,
            "redacted_output": self.redacted_output,
            "timestamp": created_at,
            "turn_id": self.turn_id,
            "request_id": self.request_id,
            "tool_call_id": self.tool_call_id,
            "approval_scope": self.approval_scope,
            "approval_decision": self.approval_decision,
            "capability": self.capability,
            "target_kind": self.target_kind,
            "target_value": self.target_value,
            "matched_rule": self.matched_rule,
            "approval_source": self.approval_source,
            "approval_reviewer": self.approval_reviewer,
            "approval_review_status": self.approval_review_status,
            "approval_rationale": self.approval_rationale,
            "approval_risk": self.approval_risk,
            "approval_authorization": self.approval_authorization,
            "approval_grant_id": self.approval_grant_id,
        }


class AuditLogger(Protocol):
    def __call__(self, record: AuditRecord) -> None: ...


class JsonlAuditLogger:
    def __init__(self, path: Path) -> None:
        self.path = path

    def write(self, record: AuditRecord) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")


def redact_text(value: object) -> str:
    text = str(value)
    for pattern in SECRET_PATTERNS:
        text = pattern.sub(_redact_match, text)
    return text


def _redact_match(match: re.Match[str]) -> str:
    return (match.groupdict().get("prefix") or "") + "[REDACTED]"


def redact_json(value: object) -> object:
    """递归脱敏文本值，保留 JSON 的键、数值和容器结构。"""
    if value is None or isinstance(value, int | float | bool):
        return value
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list | tuple):
        return [redact_json(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): redact_json(item) for key, item in value.items()}
    return redact_text(value)
