from __future__ import annotations

from cade.agent.types import ApprovalScope
from cade.harness.security import HITLDecision, HITLResult, HITLScope

_SCOPE_CHOICE = {
    "once": "Allow (once)",
    "session": "Allow this session",
    "permanent": "Always allow",
}


def hitl_choices(allowed_scopes: tuple[ApprovalScope, ...]) -> tuple[str, ...]:
    """将引擎允许的授权范围转换成界面选项。"""
    return tuple(_SCOPE_CHOICE[scope] for scope in allowed_scopes) + ("Deny",)


HITL_CHOICES = hitl_choices(("once", "session", "permanent"))


def parse_hitl_choice(text: str) -> HITLResult | None:
    """将用户输入的权限选择文本解析为 HITLResult。"""
    normalized = text.strip().lower()
    mapping: dict[str, tuple[HITLDecision, HITLScope]] = {
        "deny": ("deny", "once"),
        "allow (once)": ("allow", "once"),
        "allow once": ("allow", "once"),
        "allow this session": ("allow", "session"),
        "always allow": ("allow", "permanent"),
    }
    pair = mapping.get(normalized)
    if pair is None:
        return None
    return HITLResult(*pair)
