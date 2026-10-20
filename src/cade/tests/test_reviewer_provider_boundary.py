"""通过真实适配器验证审批缓存路由、重放和并发参数隔离。

合成 Responses 和 Chat Completions 载荷遵循各自的公开流式协议。
独立检查第二次线请求中的完整输出项和缓存身份，防止仅文本回放造成前缀变化。
无需外部服务；运行 pytest -q 本文件。
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from cade.agent.types import (
    ApprovalContext,
    ApprovalEvidence,
    ApprovalEvidenceEntry,
    ApprovalRequest,
    ToolSpec,
)
from cade.ai.providers.openai import OpenAIChatProvider
from cade.ai.providers.responses import OpenAICodexResponsesProvider
from cade.ai.types import ProviderConfig
from cade.harness.security.approval_reviewer import AutoApprovalReviewer

_VERDICT = json.dumps(
    {
        "outcome": "allow",
        "risk_level": "low",
        "user_authorization": "high",
        "rationale": "Scoped inspection",
    }
)
_TOOL = ToolSpec("bash", "Run command", "command", lambda args, update: "")


def _request(identity: str) -> ApprovalRequest:
    return ApprovalRequest(
        _TOOL,
        {"command": "pwd"},
        ("once",),
        "ask",
        context=ApprovalContext(
            "session",
            identity,
            "policy",
            ApprovalEvidence(
                messages=(
                    ApprovalEvidenceEntry("user", "user", "Inspect the directory"),
                )
            ),
            lambda: True,
        ),
    )


class _Responses:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def create(self, **params: Any) -> list[dict[str, Any]]:
        self.requests.append(params)
        return [
            {"type": "response.output_text.delta", "delta": _VERDICT},
            {
                "type": "response.completed",
                "response": {
                    "id": "resp_review",
                    "status": "completed",
                    "output": [
                        {
                            "type": "reasoning",
                            "id": "reason_review",
                            "encrypted_content": "opaque-review-data",
                            "summary": [],
                        },
                        {
                            "type": "message",
                            "id": "msg_review",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": _VERDICT}],
                        },
                    ],
                    "usage": {
                        "input_tokens": 2000,
                        "input_tokens_details": {"cached_tokens": 1792},
                        "output_tokens": 40,
                        "output_tokens_details": {"reasoning_tokens": 10},
                    },
                },
            },
        ]


def test_reviewer_replays_native_response_and_routes_cache() -> None:
    boundary = _Responses()
    provider = OpenAICodexResponsesProvider(
        ProviderConfig(
            api_key="",
            model="gpt-6-luna",
            base_url="https://chatgpt.com/backend-api",
            thinking=True,
        ),
        client=SimpleNamespace(responses=boundary),
    )
    reviewer = AutoApprovalReviewer(provider)
    try:
        first = reviewer(_request("one"))
        second = reviewer(_request("two"))
        assert first.decision == second.decision == "allow"
        a, b = boundary.requests
        assert a["prompt_cache_key"] == b["prompt_cache_key"] == "reviewer:session"
        assert b["input"][: len(a["input"])] == a["input"]
        assert any(
            item.get("encrypted_content") == "opaque-review-data" for item in b["input"]
        )
        assert reviewer.metrics[-1].cached_input_tokens == 1792
    finally:
        reviewer.close()


@pytest.mark.parametrize(
    "finish_reason, expected", [("stop", "allow"), ("length", "deny")]
)
def test_chat_reviewer_requires_complete_verdict(
    finish_reason: str, expected: str
) -> None:
    def create(**kwargs: object) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(
                usage=None,
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(content=_VERDICT, tool_calls=None),
                        finish_reason=finish_reason,
                    )
                ],
            )
        ]

    provider = OpenAIChatProvider(
        ProviderConfig(api_key="", model="test", thinking=False),
        client=SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        ),
    )
    reviewer = AutoApprovalReviewer(provider)
    try:
        assert reviewer(_request("one")).decision == expected
    finally:
        reviewer.close()
