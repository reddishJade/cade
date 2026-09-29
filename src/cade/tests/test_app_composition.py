"""真实应用组装与 session 回放契约测试。"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest

from cade.ai.events import FinalMessage, Message, ProviderEvent, TextDelta
from cade.ai.providers.registry import ProviderBundle
from cade.ai.types import StreamOptions, ToolDefinition
from cade.coding_agent.app import build_app
from cade.harness.config import CadeRuntimeConfig, ExecutionModesRuntimeConfig


class _ContractProvider:
    model = "contract-model"
    base_url = "https://contract.invalid"
    transport = "contract"
    thinking = False
    reasoning_effort = None

    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.requests: list[tuple[list[Message], list[ToolDefinition]]] = []

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolDefinition],
        options: StreamOptions | None = None,
        **kwargs: object,
    ):
        del options, kwargs
        self.requests.append((deepcopy(messages), deepcopy(tools)))
        yield cast(ProviderEvent, TextDelta(self.answer))
        yield cast(
            ProviderEvent,
            FinalMessage(content=self.answer, stop_reason="end_turn"),
        )


def _install_contract_provider(
    monkeypatch: pytest.MonkeyPatch,
    providers: list[_ContractProvider],
) -> None:
    def build_bundle(_settings: object) -> ProviderBundle:
        provider = _ContractProvider(f"answer-{len(providers) + 1}")
        providers.append(provider)
        typed = cast(Any, provider)
        return ProviderBundle(
            llm=typed,
            llms={
                "main": typed,
                "subagent": typed,
                "judge": typed,
                "refiner": typed,
            },
        )

    monkeypatch.setattr("cade.coding_agent.app.build_provider_bundle", build_bundle)


def _provider_request_events(app: Any) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for entry in app.session_store.build_branch():
        if (
            entry.type == "event"
            and isinstance(entry.content, dict)
            and entry.content.get("type") == "provider_request"
        ):
            events.append(entry.content)
    return events


def test_real_build_app_minimal_run_and_replay_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    providers: list[_ContractProvider] = []
    _install_contract_provider(monkeypatch, providers)
    sessions_dir = tmp_path / "sessions"
    runtime_config = CadeRuntimeConfig()

    first = build_app(
        tmp_path,
        runtime_config=runtime_config,
        sessions_dir=sessions_dir,
    )
    first_answer = first.ask("first question")
    session_id = first.session_store.session_id
    first_branch = first.session_store.build_branch()

    assert first_answer == "answer-1"
    assert first.agent.current_mode == "act"
    assert all(entry.type != "user" for entry in first_branch)
    assert [
        entry.content.get("type")
        for entry in first_branch
        if entry.type == "event" and isinstance(entry.content, dict)
    ][:2] == ["inbox/inserted", "inbox/claimed"]
    assert first.registry
    assert {tool.name for tool in first.registry} >= {
        "read",
        "bash",
        "delegate",
    }
    assert "patch" in first.agent.composition.gate.tool_path_extractors
    assert "apply_patch" not in first.agent.composition.gate.tool_path_extractors
    first_request = providers[0].requests[0]
    first_tool_names = {tool.name for tool in first_request[1]}
    assert {"read", "write", "edit", "patch", "bash"} <= first_tool_names
    assert not first_tool_names & {"grep", "glob", "find", "ls", "search_tools"}
    first_envelope = _provider_request_events(first)[0]["data"]
    assert first_envelope["composition_id"] == first.agent.composition.generation_id
    assert first_envelope["message_count"] == len(first_request[0])
    assert first_envelope["tool_count"] == len(first_request[1])
    assert len(first_envelope["request_digest"]) == 64
    assert first_envelope["request_bytes"] > 0
    assert "messages" not in first_envelope
    assert "tools" not in first_envelope
    first.close()

    resumed = build_app(
        tmp_path,
        runtime_config=runtime_config,
        sessions_dir=sessions_dir,
    )
    resumed.session_store.resume(session_id)
    resumed.restore_session()
    second_answer = resumed.ask("second question")

    assert second_answer == "answer-2"
    second_messages = providers[1].requests[0][0]
    assert any(
        message.get("role") == "user" and message.get("content") == "first question"
        for message in second_messages
    )
    assert any(
        message.get("role") == "assistant" and message.get("content") == "answer-1"
        for message in second_messages
    )
    second_envelope = _provider_request_events(resumed)[-1]["data"]
    assert second_envelope["message_count"] == len(second_messages)
    assert len(second_envelope["request_digest"]) == 64
    resumed.close()


def test_build_app_starts_in_configured_default_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    providers: list[_ContractProvider] = []
    _install_contract_provider(monkeypatch, providers)
    sessions_dir = tmp_path / "sessions"
    runtime_config = CadeRuntimeConfig(
        execution_modes=ExecutionModesRuntimeConfig(default_mode="build")
    )

    app = build_app(
        tmp_path,
        runtime_config=runtime_config,
        sessions_dir=sessions_dir,
    )
    assert app.agent.current_mode == "build"
    assert app.agent.approvals_reviewer == "auto_review"
    assert app.agent.auto_approval_callback is not None
    assert app.agent.current_approval_callback is app.agent.auto_approval_callback

    app.ask("first question")
    first_request = providers[0].requests[0]
    assert any(
        "Build Mode is active" in str(message.get("content"))
        for message in first_request[0]
    )
    build_tool_names = {tool.name for tool in first_request[1]}
    assert {"read", "write", "edit", "patch", "bash"} <= build_tool_names
    assert not build_tool_names & {"grep", "glob", "find", "ls", "search_tools"}
    app.close()


def test_plan_provider_surface_keeps_bash_without_search_wrappers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    providers: list[_ContractProvider] = []
    _install_contract_provider(monkeypatch, providers)
    runtime_config = CadeRuntimeConfig(
        execution_modes=ExecutionModesRuntimeConfig(default_mode="plan")
    )

    app = build_app(
        tmp_path,
        runtime_config=runtime_config,
        sessions_dir=tmp_path / "sessions",
    )
    app.ask("inspect the project and make a plan")

    first_request = providers[0].requests[0]
    assert any(
        "Plan Mode is active" in str(message.get("content"))
        for message in first_request[0]
    )
    plan_tool_names = {tool.name for tool in first_request[1]}
    assert {"read", "bash", "write", "edit"} <= plan_tool_names
    assert "patch" not in plan_tool_names
    assert not plan_tool_names & {"grep", "glob", "find", "ls", "search_tools"}

    app.close()
