"""Token 估算与上下文换窗触发单元测试。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from cade.agent._context_window import (
    extract_prompt_tokens_from_usage,
)
from cade.agent.messages import (
    UserMessage,
)
from cade.harness.agent_runtime.config import _rollover_decision
from cade.harness.config import AgentConfig


def test_extract_prompt_tokens_from_usage() -> None:
    assert extract_prompt_tokens_from_usage(None) is None
    assert extract_prompt_tokens_from_usage({}) is None
    assert extract_prompt_tokens_from_usage({"prompt_tokens": 150}) == 150
    assert extract_prompt_tokens_from_usage({"prompt_tokens": "abc"}) is None


def test_runtime_rollover_uses_provider_context_window_override() -> None:
    composition = SimpleNamespace(
        config=AgentConfig(reserve_tokens=100, rollover_trigger_ratio=0.95)
    )
    provider = SimpleNamespace(model="gpt-5.5", context_window=1_000)

    def rollover(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return messages

    before = _rollover_decision(
        [], cast(Any, rollover), None, 769, cast(Any, composition), cast(Any, provider)
    )
    at_limit = _rollover_decision(
        [], cast(Any, rollover), None, 770, cast(Any, composition), cast(Any, provider)
    )

    assert before is None
    assert at_limit == "token_limit"


def test_runtime_rollover_uses_codex_transport_context_window() -> None:
    composition = SimpleNamespace(
        config=AgentConfig(reserve_tokens=0, rollover_trigger_ratio=0.95)
    )
    provider = SimpleNamespace(
        model="gpt-5.6-luna",
        transport="openai_codex",
        context_window=None,
    )
    rollover = cast(Any, lambda messages: messages)

    before = _rollover_decision(
        [], rollover, None, 257_375, cast(Any, composition), cast(Any, provider)
    )
    at_limit = _rollover_decision(
        [], rollover, None, 257_376, cast(Any, composition), cast(Any, provider)
    )

    assert before is None
    assert at_limit == "token_limit"


def test_runtime_rollover_estimates_tokens_when_usage_is_missing() -> None:
    composition = SimpleNamespace(
        config=AgentConfig(reserve_tokens=0, rollover_trigger_ratio=0.95)
    )
    provider = SimpleNamespace(model="gpt-5.5", context_window=10)

    result = _rollover_decision(
        [UserMessage(content="enough text to cross a tiny configured window")],
        cast(Any, lambda messages: messages),
        None,
        None,
        cast(Any, composition),
        cast(Any, provider),
    )

    assert result == "token_limit"


def test_runtime_rollover_prefers_current_request_estimate() -> None:
    composition = SimpleNamespace(
        config=AgentConfig(reserve_tokens=0, rollover_trigger_ratio=0.95)
    )
    provider = SimpleNamespace(model="gpt-5.5", context_window=1_000)

    result = _rollover_decision(
        [],
        cast(Any, lambda messages: messages),
        None,
        100,
        cast(Any, composition),
        cast(Any, provider),
        estimated_tokens=950,
    )

    assert result == "token_limit"


def test_runtime_rollover_does_not_reuse_previous_window_usage() -> None:
    from cade.agent.context_manager import ContextManager

    manager = ContextManager()
    manager.set_last_prompt_tokens(21_586)
    manager.complete_rollover([UserMessage(content="continue the task")])
    composition = SimpleNamespace(
        config=AgentConfig(reserve_tokens=4_096, rollover_trigger_ratio=0.95)
    )
    provider = SimpleNamespace(model="deepseek-flash", context_window=24_576)

    result = _rollover_decision(
        manager.history_messages(),
        cast(Any, lambda messages: messages),
        None,
        manager.token_usage.last_prompt_tokens,
        cast(Any, composition),
        cast(Any, provider),
        estimated_tokens=10_000,
    )

    assert result is None
