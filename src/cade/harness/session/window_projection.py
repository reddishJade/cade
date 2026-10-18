"""带版本、由事实推导的上下文窗口投影。

换窗记录只保存策略输入，不保存生成的模型消息。旧的 replacement
记录由 session.surface 单独兼容解码。
"""

from __future__ import annotations

from dataclasses import asdict, fields

from cade.agent._tool_pairing import repair_tool_pairing
from cade.agent.context_policy import ContextPolicy
from cade.agent.messages import AgentMessage

from .types import JsonValue

WINDOW_PROJECTION_VERSION = 1

_INT_FIELDS = frozenset(
    {
        "output_reserve",
        "rollover_token_limit",
        "next_turn_input_tokens",
    }
)
_OPTIONAL_INT_FIELDS = frozenset(
    {
        "physical_window",
        "headroom_tokens",
        "evidence_token_budget",
        "working_set_token_budget",
    }
)
_BOOL_FIELDS = frozenset({"automatic_rollover", "output_limit_supported"})


def encode_rollover_spec(
    policy: ContextPolicy | None,
    *,
    preserve_user_request: bool = True,
) -> dict[str, JsonValue]:
    """持久化可复现的策略决策，不复制投影结果。"""
    return {
        "version": WINDOW_PROJECTION_VERSION,
        "strategy": "cade_policy",
        "preserve_user_request": preserve_user_request,
        "policy": asdict(policy or ContextPolicy()),
    }


def apply_rollover_spec(
    source: list[AgentMessage],
    spec: object,
    window_id: str,
) -> list[AgentMessage]:
    """根据之前的持久会话事实重放窗口边界。"""
    from cade.harness.agent_runtime.context_window import ContextWindowRollover

    if not isinstance(spec, dict):
        raise TypeError("window projection spec must be an object")
    if (
        type(spec.get("version")) is not int
        or spec["version"] != WINDOW_PROJECTION_VERSION
        or spec.get("strategy") != "cade_policy"
    ):
        raise ValueError("unsupported context window projection version")
    preserve = spec.get("preserve_user_request")
    if not isinstance(preserve, bool):
        raise TypeError("invalid preserve_user_request")
    policy_data = spec.get("policy")
    if not isinstance(policy_data, dict):
        raise TypeError("context window policy is missing")
    if set(policy_data) != {field.name for field in fields(ContextPolicy)}:
        raise ValueError("invalid context window policy fields")
    for name, value in policy_data.items():
        if name in _INT_FIELDS and type(value) is not int:
            raise ValueError(f"invalid context window policy {name}")
        if (
            name in _OPTIONAL_INT_FIELDS
            and value is not None
            and type(value) is not int
        ):
            raise ValueError(f"invalid context window policy {name}")
        if name in _BOOL_FIELDS and type(value) is not bool:
            raise ValueError(f"invalid context window policy {name}")
        if name == "trigger_ratio" and (type(value) not in (int, float)):
            raise ValueError("invalid context window policy trigger_ratio")
    policy = ContextPolicy(**policy_data)
    rolled = ContextWindowRollover().rollover_messages(
        source,
        policy=policy,
        preserve_user_request=preserve,
        window_id=window_id,
    )
    return repair_tool_pairing(rolled)
