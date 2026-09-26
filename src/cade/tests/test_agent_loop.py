"""Agent 核心循环纯函数单元测试。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from cade.agent.agent_loop import (
    _assistant_error_detail,
    _has_empty_text_response,
    _should_continue_max_tokens,
    _update_continuation_count,
)
from cade.agent.config import AgentLoopConfig
from cade.agent.messages import AssistantMessage
from cade.agent.types import TextContent
from cade.harness.config import AgentConfig


class TestMaxSteps:
    def test_default_is_unbounded(self) -> None:
        assert AgentLoopConfig().max_steps is None
        assert AgentConfig().max_steps is None

    @pytest.mark.parametrize("value", [0, -1])
    def test_explicit_limit_must_be_positive(self, value: int) -> None:
        with pytest.raises(ValidationError):
            AgentLoopConfig(max_steps=value)
        with pytest.raises(ValidationError):
            AgentConfig(max_steps=value)


class TestMaxLlmCalls:
    def test_default_is_unbounded(self) -> None:
        assert AgentLoopConfig().max_llm_calls is None
        assert AgentConfig().max_llm_calls is None

    @pytest.mark.parametrize("value", [0, -1])
    def test_explicit_limit_must_be_positive(self, value: int) -> None:
        with pytest.raises(ValidationError):
            AgentLoopConfig(max_llm_calls=value)
        with pytest.raises(ValidationError):
            AgentConfig(max_llm_calls=value)


class TestShouldContinueMaxTokens:
    def test_continuation_disabled(self) -> None:
        config = AgentLoopConfig(max_tokens_continuation=False)
        assert not _should_continue_max_tokens("max_tokens", config)


class TestUpdateContinuationCount:
    def test_short_output_increments(self) -> None:
        msg = AssistantMessage(
            content=[TextContent(text="short")],
            stop_reason="max_tokens",
        )
        result = _update_continuation_count(
            msg, 0, AgentLoopConfig(min_continuation_tokens=500)
        )
        assert result == 1

    def test_long_output_resets(self) -> None:
        msg = AssistantMessage(
            content=[TextContent(text="x" * 5000)],
            stop_reason="max_tokens",
        )
        result = _update_continuation_count(
            msg, 0, AgentLoopConfig(min_continuation_tokens=500)
        )
        assert result == 0

    def test_exceeds_limit_returns_none(self) -> None:
        msg = AssistantMessage(
            content=[TextContent(text="short")],
            stop_reason="max_tokens",
        )
        result = _update_continuation_count(
            msg,
            2,
            AgentLoopConfig(
                min_continuation_tokens=500, max_consecutive_continuations=3
            ),
        )
        assert result is None


class TestHasEmptyTextResponse:
    def test_empty_content(self) -> None:
        msg = AssistantMessage(content=[], stop_reason="error")
        assert _has_empty_text_response(msg)

    def test_empty_text(self) -> None:
        msg = AssistantMessage(
            content=[TextContent(text="")],
            stop_reason="error",
        )
        assert _has_empty_text_response(msg)

    def test_non_empty_text(self) -> None:
        msg = AssistantMessage(
            content=[TextContent(text="hello")],
            stop_reason="error",
        )
        assert not _has_empty_text_response(msg)


class TestAssistantErrorDetail:
    def test_from_text_content(self) -> None:
        msg = AssistantMessage(
            content=[TextContent(text="Something went wrong")],
        )
        assert _assistant_error_detail(msg) == "Something went wrong"

    def test_none(self) -> None:
        msg = AssistantMessage()
        assert _assistant_error_detail(msg) is None
