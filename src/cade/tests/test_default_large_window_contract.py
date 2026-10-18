"""默认长上下文准入与换窗边界回归测试。

不需要外部模型，provider 直接上报物理上下文窗口。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from cade.agent.messages import AssistantMessage, UserMessage
from cade.agent.types import TextContent
from cade.harness.agent_runtime.config import resolve_context_policy
from cade.harness.agent_runtime.context_window import ContextWindowRollover
from cade.harness.config import AgentConfig


@pytest.mark.parametrize("physical_window", [128_000, 272_000, 1_000_000])
def test_default_long_context_rotation_boundary(physical_window: int) -> None:
    provider = SimpleNamespace(
        model="gpt-5.6-luna",
        transport="openai_codex",
        context_window=physical_window,
        supports_output_token_limit=False,
    )
    policy = resolve_context_policy(provider, AgentConfig())
    expected_headroom = min(8192, max(1, physical_window // 50))
    assert policy.physical_window == physical_window
    assert policy.input_budget == physical_window - 16_384 - expected_headroom
    assert policy.rotation_threshold == min(
        policy.input_budget, int(physical_window * 0.95)
    )
    assert policy.admission_target == (
        policy.rotation_threshold - policy.next_input_allowance
    )
    assert not policy.should_rotate(policy.admission_target - 1)
    assert policy.should_rotate(policy.admission_target)
    assert policy.should_rotate(policy.rotation_threshold)


def test_explicit_disable_prevents_default_rollover() -> None:
    provider = SimpleNamespace(context_window=272_000, supports_output_token_limit=True)
    policy = resolve_context_policy(provider, AgentConfig(automatic_rollover=False))
    assert not policy.should_rotate(policy.rotation_threshold + 20_000)


def test_default_large_window_keeps_current_task_on_rollover() -> None:
    provider = SimpleNamespace(
        context_window=272_000, supports_output_token_limit=False
    )
    policy = resolve_context_policy(provider, AgentConfig())
    messages = [
        UserMessage(content="Prior task"),
        AssistantMessage(content=[TextContent(text="Prior answer")]),
        UserMessage(content="Current task"),
    ]
    rolled = ContextWindowRollover().rollover_messages(messages, policy=policy)
    assert any(
        isinstance(message, UserMessage) and message.content == "Current task"
        for message in rolled
    )
    assert any(
        getattr(message, "role", "") == "system"
        and "context-window-reset" in str(message.content)
        for message in rolled
    )
