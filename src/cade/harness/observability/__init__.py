"""审计、关联与钩子能力。

权限相关类型请从 `cade.harness.security` 导入。
"""

from .audit import AuditLogger, AuditRecord, JsonlAuditLogger, redact_json, redact_text
from .correlation import (
    EventCorrelation,
    HookCorrelationFields,
    RuntimeCorrelation,
    hook_correlation_fields,
)
from .external_hooks import (
    ExternalHookDiagnostic,
    ExternalHookExecution,
    ExternalHookFailure,
    ExternalHookRunner,
)
from .hooks import (
    HookManager,
    HookRecord,
    SignalHookManager,
)

__all__ = [
    "AuditLogger",
    "AuditRecord",
    "EventCorrelation",
    "ExternalHookDiagnostic",
    "ExternalHookExecution",
    "ExternalHookFailure",
    "ExternalHookRunner",
    "HookCorrelationFields",
    "HookManager",
    "HookRecord",
    "JsonlAuditLogger",
    "RuntimeCorrelation",
    "SignalHookManager",
    "hook_correlation_fields",
    "redact_json",
    "redact_text",
]
