"""保护 Responses 完成项的请求重放、预算与会话恢复契约。"""

from __future__ import annotations

import shutil
import uuid
from collections import deque
from collections.abc import Generator, Mapping
from pathlib import Path
from typing import Any

import pytest
from openai.types.responses import ResponseFunctionToolCall

from cade.agent._codec import convert_to_llm
from cade.agent._context_window import estimate_wire_message_tokens
from cade.agent._tool_pairing import repair_tool_pairing
from cade.agent.agent_loop import run_agent_loop
from cade.agent.config import AgentContext, AgentLoopConfig
from cade.agent.context import (
    ContextBlock,
    ContextBlockSource,
    ContextCollectorRegistry,
    ContextPriority,
    ContextSection,
)
from cade.agent.context_manager import history_fingerprints
from cade.agent.context_policy import ContextPolicy
from cade.agent.events import AgentEvent
from cade.agent.messages import (
    AgentMessage,
    AssistantMessage,
    ContextMessage,
    ToolResultMessage,
    UserMessage,
)
from cade.agent.request import DefaultRequestAssembler, RequestAssembler
from cade.agent.results import AgentLoopResult
from cade.agent.types import (
    AgentToolResult,
    CancellationSignal,
    TextContent,
    ToolArguments,
    ToolCallContent,
    ToolExecutionMode,
    ToolUpdateCallback,
)
from cade.ai.events import (
    FinalMessage,
    ProviderEvent,
    ProviderJsonValue,
    ProviderReplayPayload,
    ToolCallEvent,
)
from cade.ai.providers.responses import (
    OpenAICodexResponsesProvider,
    OpenAIResponsesProvider,
    to_responses_input,
)
from cade.ai.types import ProviderConfig, StreamOptions
from cade.harness.agent_runtime.agent_helpers import to_dict
from cade.harness.agent_runtime.events import (
    _StreamTranslationState,
    translate_event,
)
from cade.harness.agent_runtime.message_codec import messages_from_provider_dicts
from cade.harness.session import InboxLane, SessionInbox, SessionStore
from cade.harness.session.recorder import SessionRecorder
from cade.harness.session.replay import replay_session
from cade.harness.session.surface import InvalidSessionSurfaceError


def _reasoning_item() -> dict[str, ProviderJsonValue]:
    return {
        "id": "rs_1",
        "type": "reasoning",
        "status": "completed",
        "encrypted_content": "sanitized-encrypted-placeholder",
        "summary": [{"type": "summary_text", "text": "Checking the value."}],
    }


def _message_item(item_id: str, text: str, phase: str) -> dict[str, ProviderJsonValue]:
    return {
        "id": item_id,
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "phase": phase,
        "content": [{"type": "output_text", "text": text}],
    }


def _function_item() -> dict[str, ProviderJsonValue]:
    return {
        "id": "fc_item_1",
        "type": "function_call",
        "status": "completed",
        "phase": "commentary",
        "call_id": "call_echo_1",
        "name": "echo",
        "arguments": '{"value":"saved"}',
    }


class _ResponsesBoundary:
    def __init__(self, streams: list[list[object]]) -> None:
        self._streams = deque(streams)
        self.requests: list[dict[str, Any]] = []

    def create(self, **params: Any) -> list[object]:
        self.requests.append(params)
        return self._streams.popleft()


class _InjectedClient:
    def __init__(self, streams: list[list[object]]) -> None:
        self.responses = _ResponsesBoundary(streams)


def _stream_for_items(
    items: list[dict[str, ProviderJsonValue]],
    *,
    response_id: str,
    text_deltas: Mapping[str, str] | None = None,
    reasoning_tokens: int = 6,
    output_tokens: int = 24,
    terminal_type: str = "response.completed",
    status: str = "completed",
) -> list[object]:
    events: list[object] = []
    for index, item in enumerate(items):
        item_id = str(item.get("id", ""))
        added_item = dict(item)
        if item.get("type") == "reasoning":
            added_item["encrypted_content"] = "partial-encrypted-placeholder"
        events.append(
            {
                "type": "response.output_item.added",
                "output_index": index,
                "item": added_item,
            }
        )
        if item.get("type") == "function_call":
            arguments = str(item.get("arguments", "{}"))
            events.append(
                {
                    "type": "response.function_call_arguments.delta",
                    "item_id": item_id,
                    "delta": arguments,
                }
            )
            events.append(
                {
                    "type": "response.function_call_arguments.done",
                    "item_id": item_id,
                    "arguments": arguments,
                }
            )
        delta = (text_deltas or {}).get(item_id)
        if delta:
            events.append({"type": "response.output_text.delta", "delta": delta})
        events.append(
            {
                "type": "response.output_item.done",
                "output_index": index,
                "item": item,
            }
        )
    events.append(
        {
            "type": terminal_type,
            "response": {
                "id": response_id,
                "status": status,
                "output": [],
                "usage": {
                    "input_tokens": 80,
                    "output_tokens": output_tokens,
                    "input_tokens_details": {"cached_tokens": 32},
                    "output_tokens_details": {"reasoning_tokens": reasoning_tokens},
                },
            },
        }
    )
    return events


def _provider(
    streams: list[list[object]], model: str = "gpt-6-sol"
) -> tuple[OpenAIResponsesProvider, _ResponsesBoundary]:
    client = _InjectedClient(streams)
    provider = OpenAIResponsesProvider(
        ProviderConfig(
            api_key="",
            model=model,
            base_url="https://api.openai.com/v1",
            thinking=True,
            reasoning_effort="medium",
        ),
        client=client,
    )
    return provider, client.responses


async def _collect_provider(
    provider: OpenAIResponsesProvider,
    options: StreamOptions | None = None,
) -> list[ProviderEvent]:
    return [
        event
        async for event in provider.stream(
            [{"role": "user", "content": "Continue."}], [], options
        )
    ]


@pytest.mark.asyncio
async def test_responses_adapter_captures_sdk_wire_aliases_and_item_order() -> None:
    sdk_function = ResponseFunctionToolCall(
        id="fc_sdk_1",
        type="function_call",
        status="completed",
        call_id="call_sdk_1",
        name="echo",
        arguments='{"value":"sdk"}',
        async_=True,
    )
    reasoning = _reasoning_item()
    message = _message_item("msg_sdk_1", "Checking.", "commentary")
    function_wire: dict[str, ProviderJsonValue] = {
        "id": "fc_sdk_1",
        "type": "function_call",
        "status": "completed",
        "call_id": "call_sdk_1",
        "name": "echo",
        "arguments": '{"value":"sdk"}',
        "async": True,
    }
    items = [reasoning, message, function_wire]
    events = [
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {**reasoning, "encrypted_content": "partial-placeholder"},
        },
        {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": reasoning,
        },
        {
            "type": "response.output_item.added",
            "output_index": 1,
            "item": message,
        },
        {"type": "response.output_text.delta", "delta": "Checking."},
        {
            "type": "response.output_item.done",
            "output_index": 1,
            "item": message,
        },
        {
            "type": "response.output_item.added",
            "output_index": 2,
            "item": sdk_function,
        },
        {
            "type": "response.output_item.done",
            "output_index": 2,
            "item": sdk_function,
        },
        {
            "type": "response.completed",
            "response": {
                "id": "resp_sdk_1",
                "status": "completed",
                "output": items,
                "usage": {
                    "input_tokens": 80,
                    "output_tokens": 24,
                    "output_tokens_details": {"reasoning_tokens": 6},
                },
            },
        },
    ]
    provider, _ = _provider([events])

    result = await _collect_provider(provider)

    final = next(event for event in result if isinstance(event, FinalMessage))
    assert final.replay_payload is not None
    assert final.replay_payload.output_items == tuple(items)
    assert final.replay_payload.output_items[1]["phase"] == "commentary"
    assert "async" in final.replay_payload.output_items[2]
    assert "async_" not in final.replay_payload.output_items[2]
    assert "namespace" not in final.replay_payload.output_items[2]


@pytest.mark.asyncio
async def test_responses_replay_is_gated_and_does_not_duplicate_visible_content() -> (
    None
):
    raw_items = (_reasoning_item(), _message_item("msg_1", "Visible.", "final_answer"))
    assistant = AssistantMessage(
        content=[TextContent(text="Visible.")],
        reasoning_content="Visible summary.",
        replay_payload=ProviderReplayPayload(
            protocol="openai_responses",
            transport="openai_responses",
            model="gpt-6-sol",
            output_items=raw_items,
            reasoning_tokens=7,
            output_tokens=20,
        ),
    )
    wire = convert_to_llm([assistant])

    compatible = to_responses_input(
        wire, transport="openai_responses", model="gpt-6-sol"
    )
    other_model = to_responses_input(
        wire, transport="openai_responses", model="gpt-6.1-sol"
    )
    other_transport = to_responses_input(
        wire, transport="openai_codex", model="gpt-6-sol"
    )

    assert compatible == list(raw_items)
    assert len(compatible) == 2
    assert sum(item.get("type") == "message" for item in compatible) == 1
    assert "Visible summary." not in str(compatible)
    assert other_model[0]["type"] == "message"
    assert other_transport[0]["type"] == "message"
    assert "encrypted_content" not in str(other_model)
    assert "encrypted_content" not in str(other_transport)


def test_repair_tool_pairing_cannot_resurrect_an_orphan_replay_call() -> None:
    reasoning_item: dict[str, ProviderJsonValue] = {
        "id": "rs_retained",
        "type": "reasoning",
        "status": "completed",
        "encrypted_content": "encrypted reasoning depends on the original calls",
    }
    message_item = _message_item("msg_retained", "Retained text.", "final_answer")
    retained_item: dict[str, ProviderJsonValue] = {
        "id": "fc_retained",
        "type": "function_call",
        "status": "completed",
        "call_id": "call_retained",
        "name": "echo",
        "arguments": '{"value":"kept"}',
    }
    orphan_item: dict[str, ProviderJsonValue] = {
        "id": "fc_orphan",
        "type": "function_call",
        "status": "completed",
        "call_id": "call_orphan",
        "name": "echo",
        "arguments": '{"value":"deleted"}',
    }
    assistant = AssistantMessage(
        content=[
            TextContent(text="Retained text."),
            ToolCallContent(
                id="call_retained", name="echo", arguments={"value": "kept"}
            ),
            ToolCallContent(
                id="call_orphan", name="echo", arguments={"value": "deleted"}
            ),
        ],
        replay_payload=ProviderReplayPayload(
            protocol="openai_responses",
            transport="openai_responses",
            model="gpt-6-sol",
            output_items=(reasoning_item, message_item, retained_item, orphan_item),
            reasoning_tokens=4,
        ),
    )

    repaired = repair_tool_pairing(
        [
            assistant,
            ToolResultMessage(
                tool_call_id="call_retained", tool_name="echo", content="kept"
            ),
        ]
    )
    repaired_assistant = next(
        message for message in repaired if isinstance(message, AssistantMessage)
    )
    assert [
        block for block in repaired_assistant.content if isinstance(block, TextContent)
    ] == [TextContent(text="Retained text.")]
    assert [
        block.id
        for block in repaired_assistant.content
        if isinstance(block, ToolCallContent)
    ] == ["call_retained"]
    assert repaired_assistant.replay_payload is None
    replayed = to_responses_input(
        convert_to_llm([repaired_assistant]),
        transport="openai_responses",
        model="gpt-6-sol",
    )
    assert replayed == [
        {
            "type": "function_call",
            "call_id": "call_retained",
            "name": "echo",
            "arguments": '{"value":"kept"}',
        },
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "Retained text."}],
        },
    ]

    text_only = AssistantMessage(
        content=[
            TextContent(text="Still visible."),
            ToolCallContent(
                id="call_orphan", name="echo", arguments={"value": "deleted"}
            ),
        ],
        replay_payload=ProviderReplayPayload(
            protocol="openai_responses",
            transport="openai_responses",
            model="gpt-6-sol",
            output_items=(orphan_item,),
            reasoning_tokens=4,
        ),
    )
    repaired_text = repair_tool_pairing([text_only])[0]
    assert isinstance(repaired_text, AssistantMessage)
    assert repaired_text.replay_payload is None
    assert to_responses_input(
        convert_to_llm([repaired_text]),
        transport="openai_responses",
        model="gpt-6-sol",
    ) == [
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "Still visible."}],
        }
    ]

    unrepaired_wire = convert_to_llm(
        [assistant.model_copy(update={"content": assistant.content[:2]})]
    )[0]
    fallback = to_responses_input(
        [unrepaired_wire], transport="openai_responses", model="gpt-6-sol"
    )
    assert [
        item.get("call_id") for item in fallback if item.get("type") == "function_call"
    ] == ["call_retained"]


@pytest.mark.asyncio
async def test_completed_response_output_fills_missing_stream_items() -> None:
    array_item = _reasoning_item()
    array_call = _function_item()
    streamed_item = _message_item("msg_streamed", "Stream item.", "commentary")
    unfinished_item: dict[str, ProviderJsonValue] = {
        "id": "rs_unfinished_stream",
        "type": "reasoning",
        "status": "in_progress",
        "encrypted_content": "unfinished-placeholder",
    }
    events = [
        {
            "type": "response.output_item.added",
            "output_index": 3,
            "item": unfinished_item,
        },
        {
            "type": "response.output_item.done",
            "output_index": 2,
            "item": streamed_item,
        },
        {
            "type": "response.completed",
            "response": {
                "id": "resp_array_fallback",
                "status": "completed",
                "output": [array_item, array_call],
                "usage": {
                    "output_tokens": 24,
                    "output_tokens_details": {"reasoning_tokens": 6},
                },
            },
        },
    ]
    provider, _ = _provider([events])

    result = await _collect_provider(provider)

    final = next(event for event in result if isinstance(event, FinalMessage))
    assert final.replay_payload is not None
    assert final.replay_payload.output_items == (array_item, array_call, streamed_item)
    calls = next(event for event in result if isinstance(event, ToolCallEvent))
    assert [(call.id, call.name, call.input) for call in calls.calls] == [
        ("call_echo_1", "echo", {"value": "saved"})
    ]


@pytest.mark.asyncio
async def test_completed_response_output_skips_explicitly_unfinished_items() -> None:
    completed_item = _message_item("msg_complete", "Complete.", "final_answer")
    unfinished_item = _message_item("msg_unfinished", "Partial.", "final_answer")
    unfinished_item["status"] = "in_progress"
    provider, _ = _provider(
        [
            [
                {
                    "type": "response.completed",
                    "response": {
                        "id": "resp_unfinished_item",
                        "status": "completed",
                        "output": [completed_item, unfinished_item],
                    },
                }
            ]
        ]
    )

    result = await _collect_provider(provider)

    final = next(event for event in result if isinstance(event, FinalMessage))
    assert final.replay_payload is not None
    assert final.replay_payload.output_items == (completed_item,)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "events",
    [
        [
            {
                "type": "response.output_item.added",
                "output_index": 0,
                "item": {
                    "id": "rs_partial",
                    "type": "reasoning",
                    "encrypted_content": "partial-ciphertext-placeholder",
                },
            }
        ],
        [
            {
                "type": "response.output_item.added",
                "output_index": 0,
                "item": {
                    "id": "fc_partial",
                    "type": "function_call",
                    "call_id": "call_partial",
                    "name": "echo",
                    "arguments": "",
                },
            },
            {
                "type": "response.function_call_arguments.delta",
                "item_id": "fc_partial",
                "delta": '{"value":"unfinished"}',
            },
            {
                "type": "response.incomplete",
                "response": {
                    "id": "resp_incomplete",
                    "status": "incomplete",
                    "output": [],
                },
            },
        ],
    ],
)
async def test_interrupted_or_incomplete_responses_never_replay_partial_items(
    events: list[object],
) -> None:
    provider, _ = _provider([events])

    result = await _collect_provider(provider)

    final = next(event for event in result if isinstance(event, FinalMessage))
    assert final.replay_payload is None
    assert final.stop_reason == "max_tokens"
    assert not any(isinstance(event, ToolCallEvent) for event in result)


def test_replay_budget_uses_usage_without_tokenizing_ciphertext_or_double_counting() -> (
    None
):
    def make_message(
        ciphertext: str,
        *,
        reasoning_tokens: int | None,
        output_tokens: int | None,
        text: str = "visible text",
    ) -> dict[str, object]:
        message = AssistantMessage(
            content=[
                TextContent(text=text),
                ToolCallContent(
                    id="call_1", name="echo", arguments={"value": "visible arg"}
                ),
            ],
            reasoning_content="visible summary",
            replay_payload=ProviderReplayPayload(
                protocol="openai_responses",
                transport="openai_responses",
                model="gpt-6-sol",
                output_items=(
                    {
                        "id": "rs_1",
                        "type": "reasoning",
                        "encrypted_content": ciphertext,
                    },
                ),
                reasoning_tokens=reasoning_tokens,
                output_tokens=output_tokens,
            ),
        )
        return convert_to_llm([message])[0]

    count = lambda value: len(value)
    first = estimate_wire_message_tokens(
        make_message("ciphertext-a", reasoning_tokens=9, output_tokens=40), count
    )
    changed_ciphertext = estimate_wire_message_tokens(
        make_message("x" * 1000, reasoning_tokens=9, output_tokens=40), count
    )
    changed_reasoning_budget = estimate_wire_message_tokens(
        make_message("ciphertext-a", reasoning_tokens=15, output_tokens=40), count
    )
    output_fallback = estimate_wire_message_tokens(
        make_message("ciphertext-a", reasoning_tokens=None, output_tokens=40), count
    )
    sourced_output = estimate_wire_message_tokens(
        make_message("ciphertext-a", reasoning_tokens=3, output_tokens=200), count
    )
    sourced_output_with_changed_ciphertext = estimate_wire_message_tokens(
        make_message("x" * 1000, reasoning_tokens=3, output_tokens=200), count
    )

    assert changed_ciphertext == first
    assert changed_reasoning_budget == first + 6
    assert output_fallback == 44
    assert sourced_output == 204
    assert sourced_output_with_changed_ciphertext == sourced_output


def test_unbudgeted_reasoning_is_not_admitted_to_responses_replay() -> None:
    assistant = AssistantMessage(
        content=[TextContent(text="Visible answer.")],
        replay_payload=ProviderReplayPayload(
            protocol="openai_responses",
            transport="openai_responses",
            model="unknown-model",
            output_items=(
                {
                    "id": "rs_unbudgeted",
                    "type": "reasoning",
                    "encrypted_content": "x" * 100_000,
                },
            ),
        ),
    )
    wire = convert_to_llm([assistant])[0]

    replayed = to_responses_input(
        [wire], transport="openai_responses", model="unknown-model"
    )

    assert replayed == [
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "Visible answer."}],
        }
    ]
    assert estimate_wire_message_tokens(wire, lambda value: len(value)) == 4 + len(
        "Visible answer."
    )


@pytest.mark.asyncio
async def test_replay_limit_comes_from_the_final_request_params() -> None:
    events = [
        {
            "type": "response.completed",
            "response": {
                "id": "resp_limit",
                "status": "completed",
                "output": [_reasoning_item()],
            },
        }
    ]
    provider, boundary = _provider([events])

    result = await _collect_provider(provider, StreamOptions(max_tokens=20))

    final = next(event for event in result if isinstance(event, FinalMessage))
    assert boundary.requests[0]["max_output_tokens"] == 20
    assert final.replay_payload is not None
    assert final.replay_payload.output_token_limit == 20
    assert final.replay_payload.reasoning_tokens is None
    assert final.replay_payload.output_tokens is None

    codex_client = _InjectedClient([events])
    codex_provider = OpenAICodexResponsesProvider(
        ProviderConfig(
            api_key="",
            model="gpt-6-sol",
            base_url="https://api.openai.com/v1",
            thinking=True,
            reasoning_effort="medium",
        ),
        client=codex_client,
    )
    codex_options = (
        ContextPolicy()
        .for_provider(codex_provider)
        .request_options(StreamOptions(max_tokens=20))
    )
    assert codex_options is not None
    assert codex_options.max_tokens is None

    codex_result = await _collect_provider(codex_provider, codex_options)

    codex_final = next(
        event for event in codex_result if isinstance(event, FinalMessage)
    )
    assert "max_output_tokens" not in codex_client.responses.requests[0]
    assert codex_final.replay_payload is not None
    assert codex_final.replay_payload.output_token_limit is None
    codex_assistant = AssistantMessage(
        content=[TextContent(text="Visible answer.")],
        replay_payload=codex_final.replay_payload,
    )
    assert to_responses_input(
        convert_to_llm([codex_assistant]),
        transport="openai_codex",
        model="gpt-6-sol",
    ) == [
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "Visible answer."}],
        }
    ]


def test_replay_fingerprints_and_group_reclamation_follow_assistant_owner() -> None:
    def assistant(ciphertext: str, call_id: str = "call_1") -> AssistantMessage:
        return AssistantMessage(
            content=[
                TextContent(text="Called echo."),
                ToolCallContent(id=call_id, name="echo", arguments={"x": 1}),
            ],
            replay_payload=ProviderReplayPayload(
                protocol="openai_responses",
                transport="openai_responses",
                model="gpt-6-sol",
                output_items=(
                    {
                        "id": "rs_1",
                        "type": "reasoning",
                        "encrypted_content": ciphertext,
                    },
                ),
                reasoning_tokens=2_048,
                output_tokens=2_100,
            ),
        )

    original = assistant("ciphertext-a")
    changed = assistant("ciphertext-b")
    assert history_fingerprints([original]) != history_fingerprints([changed])

    result = ToolResultMessage(tool_call_id="call_1", tool_name="echo", content="1")
    recent = assistant("recent-ciphertext", call_id="call_recent")
    recent_result = ToolResultMessage(
        tool_call_id="call_recent", tool_name="echo", content="recent result"
    )
    messages: list[AgentMessage] = [
        UserMessage(content="Old request"),
        original,
        result,
        UserMessage(content="Current request"),
        recent,
        recent_result,
    ]
    policy = ContextPolicy(
        physical_window=20_000,
        output_reserve=1_000,
        automatic_rollover=False,
    )
    projected, omitted = policy.project_working_set(
        messages, frozenset(), reclaim_tokens=10_000
    )

    assert omitted == ("call_1",)
    assert original not in projected
    assert result not in projected
    assert recent in projected
    assert recent_result in projected


def test_context_window_reset_preserves_replay_payload(replay_workspace: Path) -> None:
    sessions_dir = replay_workspace / "sessions"
    store = SessionStore(sessions_dir, project_root=replay_workspace)
    assistant = AssistantMessage(
        content=[TextContent(text="Retained answer.")],
        replay_payload=ProviderReplayPayload(
            protocol="openai_responses",
            transport="openai_responses",
            model="gpt-6-sol",
            output_items=(
                _message_item("msg_rollover", "Retained answer.", "final_answer"),
            ),
            reasoning_tokens=4,
            output_tokens=12,
        ),
    )
    SessionRecorder(store).record_context_window_reset(
        window_id="window-replay",
        messages_before=3,
        messages_after=2,
        replacement=[UserMessage(content="Continue."), assistant],
    )
    resumed_store = SessionStore(sessions_dir, project_root=replay_workspace)
    resumed_store.resume(store.current_path)
    restored = _RestoredHistory()

    replay_session(restored, resumed_store)

    restored_assistant = next(
        message for message in restored.history if isinstance(message, AssistantMessage)
    )
    assert restored_assistant.replay_payload == assistant.replay_payload


class _EchoTool:
    @property
    def name(self) -> str:
        return "echo"

    @property
    def label(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "Return the supplied value."

    @property
    def parameters(self) -> Mapping[str, object]:
        return {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        }

    @property
    def execution_mode(self) -> ToolExecutionMode:
        return "sequential"

    @property
    def examples(self) -> list[dict[str, object]]:
        return []

    async def execute(
        self,
        tool_call_id: str,
        params: ToolArguments,
        signal: CancellationSignal | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        return AgentToolResult(content=[TextContent(text=str(params["value"]))])


class _RestoredHistory:
    def __init__(self) -> None:
        self.session_id = ""
        self.history: list[AgentMessage] = []

    def set_history_session_id(self, session_id: str) -> None:
        self.session_id = session_id

    def load_history(self, messages: list[AgentMessage]) -> None:
        self.history = list(messages)

    def restore_run_state_metadata(self, payload: object) -> None:
        pass

    def restore_goal_state(self, payload: object) -> None:
        pass

    def set_resumed_notice(self, notice: str) -> None:
        assert "resumed" in notice.lower()


async def _run_and_record(
    context: AgentContext,
    provider: OpenAIResponsesProvider,
    recorder: SessionRecorder,
    request_assembler: RequestAssembler | None = None,
) -> AgentLoopResult:
    result: AgentLoopResult | None = None
    events: list[AgentEvent] = []
    async for event in run_agent_loop(
        [],
        context,
        AgentLoopConfig(
            provider=provider,
            max_steps=4,
            max_llm_calls=4,
            request_assembler=request_assembler or DefaultRequestAssembler(),
        ),
    ):
        if isinstance(event, AgentLoopResult):
            result = event
        else:
            events.append(event)
    assert result is not None
    state = _StreamTranslationState()
    for event in events:
        translated = translate_event(event, state)
        translated_events = translated if isinstance(translated, list) else [translated]
        for item in translated_events:
            if item is not None:
                recorder.record_event(item)
    return result


@pytest.fixture
def replay_workspace() -> Generator[Path, None, None]:
    root = Path(__file__).resolve().parents[3] / "e2e-results"
    path = root / f"response-replay-test-{uuid.uuid4().hex}"
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.mark.asyncio
async def test_agent_session_resume_replays_completed_items_through_new_provider(
    replay_workspace: Path,
) -> None:
    reasoning = _reasoning_item()
    commentary = _message_item(
        "msg_commentary_1", "I will check the value.", "commentary"
    )
    function = _function_item()
    first_items = [reasoning, commentary, function]
    second_item = _message_item("msg_final_1", "The value is saved.", "final_answer")
    third_item = _message_item(
        "msg_final_2", "The replay survived restart.", "final_answer"
    )
    first_stream = _stream_for_items(
        first_items,
        response_id="resp_first",
        text_deltas={"msg_commentary_1": "I will check the value."},
    )
    second_stream = _stream_for_items(
        [second_item],
        response_id="resp_second",
        text_deltas={"msg_final_1": "The value is saved."},
    )
    third_stream = _stream_for_items(
        [third_item],
        response_id="resp_third",
        text_deltas={"msg_final_2": "The replay survived restart."},
    )
    provider, first_boundary = _provider([first_stream, second_stream])
    sessions_dir = replay_workspace / "sessions"
    store = SessionStore(sessions_dir, project_root=replay_workspace)
    inbox = SessionInbox(store)
    inbox.insert(
        UserMessage(content="Check the saved value."), InboxLane.NEXT_TURN, wake=True
    )
    first_user = inbox.claim_initial(run_id="run-before-restart")
    recorder = SessionRecorder(store)
    first_context = AgentContext(messages=first_user, tools=[_EchoTool()])

    first_result = await _run_and_record(first_context, provider, recorder)

    assert first_result.termination_reason.value == "completed"
    assert len(first_boundary.requests) == 2
    expected_tool_turn = [
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "Check the saved value."}],
        },
        *first_items,
        {
            "type": "function_call_output",
            "call_id": "call_echo_1",
            "output": first_boundary.requests[1]["input"][-1]["output"],
        },
    ]
    assert first_boundary.requests[1]["input"] == expected_tool_turn

    reopened_store = SessionStore(sessions_dir, project_root=replay_workspace)
    reopened_store.resume(store.current_path)
    restored = _RestoredHistory()
    replay_session(restored, reopened_store)
    assert any(
        isinstance(message, AssistantMessage) and message.replay_payload is not None
        for message in restored.history
    )
    reopened_inbox = SessionInbox(reopened_store)
    reopened_inbox.insert(
        UserMessage(content="Confirm the previous result."),
        InboxLane.NEXT_TURN,
        wake=True,
    )
    restarted_user = reopened_inbox.claim_initial(run_id="run-after-restart")
    next_provider, next_boundary = _provider([third_stream])
    next_context = AgentContext(
        messages=[*restored.history, *restarted_user],
    )

    await _run_and_record(next_context, next_provider, SessionRecorder(reopened_store))

    replayed_input = next_boundary.requests[0]["input"]
    assert replayed_input == [
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "Check the saved value."}],
        },
        *first_items,
        first_boundary.requests[1]["input"][-1],
        second_item,
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "Confirm the previous result."}],
        },
    ]
    assert (
        sum(
            item
            == {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "Confirm the previous result."}
                ],
            }
            for item in replayed_input
        )
        == 1
    )
    assert sum(item == commentary for item in replayed_input) == 1
    assert sum(item == function for item in replayed_input) == 1
    assert sum(item == second_item for item in replayed_input) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("large_result", [False, True])
async def test_runtime_snapshots_preserve_responses_prefix_across_restart_and_fork(
    replay_workspace: Path,
    large_result: bool,
) -> None:
    """覆盖缺口：加密回复的前置事实必须随会话恢复，不能只恢复回复本身。

    运行本模块；使用上述 Responses 完成项及真实适配器、循环和文件存储。
    独立比较发送边界的 input 前缀，并检查重启、分支及换窗的事实类型。
    """
    validation = {"result": "Before tool validation"}

    class UpdatingEcho(_EchoTool):
        async def execute(
            self,
            tool_call_id: str,
            params: ToolArguments,
            signal: CancellationSignal | None = None,
            on_update: ToolUpdateCallback | None = None,
        ) -> AgentToolResult:
            validation["result"] = "After tool validation"
            if large_result:
                return AgentToolResult(
                    content=[TextContent(text="Tool evidence row\n" * 4000)]
                )
            return await super().execute(tool_call_id, params, signal, on_update)

    registry = ContextCollectorRegistry()
    registry.register_section(
        ContextSection(
            "validation",
            lambda input: [
                ContextBlock(
                    source=ContextBlockSource.RECENT_VALIDATION,
                    priority=ContextPriority.CONTEXT,
                    content=str(input.state["validation"]),
                )
            ],
        )
    )
    assembler = DefaultRequestAssembler(context_collectors=registry.freeze())
    items = [
        _reasoning_item(),
        _message_item("msg_check", "Checking.", "commentary"),
        _function_item(),
    ]
    final = _message_item("msg_done", "Saved.", "final_answer")
    provider, boundary = _provider(
        [
            _stream_for_items(items, response_id="resp_check"),
            _stream_for_items([final], response_id="resp_done"),
        ]
    )
    sessions = replay_workspace / "sessions"
    store = SessionStore(sessions, project_root=replay_workspace)
    inbox = SessionInbox(store)
    inbox.insert(
        UserMessage(content="Check the value."), InboxLane.NEXT_TURN, wake=True
    )
    result = await _run_and_record(
        AgentContext(
            messages=inbox.claim_initial("before-restart"),
            tools=[UpdatingEcho()],
            state={"validation": validation},
        ),
        provider,
        SessionRecorder(store),
        assembler,
    )
    assert result.termination_reason.value == "completed"
    first_input = boundary.requests[0]["input"]
    second_input = boundary.requests[1]["input"]
    assert second_input[: len(first_input)] == first_input
    assert second_input[len(first_input) : len(first_input) + len(items)] == items
    assert "Before tool validation" in str(first_input[-1])
    assert "After tool validation" in str(second_input[-1])
    assert "Before tool validation" not in str(second_input[-1])

    reopened = SessionStore(sessions, project_root=replay_workspace)
    reopened.resume(store.current_path)
    restored = _RestoredHistory()
    replay_session(restored, reopened)
    contexts = [m for m in restored.history if isinstance(m, ContextMessage)]
    assert len(contexts) == 2
    assert "Before tool validation" in contexts[0].content
    assert "After tool validation" in contexts[1].content
    assert messages_from_provider_dicts([to_dict(m) for m in contexts]) == contexts
    artifacts = list(store.artifacts_dir.glob("*.txt"))
    if large_result:
        assert len(artifacts) == 1
        artifact = artifacts[0]
        missing = artifact.with_suffix(".missing")
        artifact.rename(missing)
        try:
            with pytest.raises(InvalidSessionSurfaceError, match="artifact is missing"):
                replay_session(_RestoredHistory(), reopened)
        finally:
            missing.rename(artifact)

    parent_bytes = reopened.current_path.read_bytes()
    fork = reopened.fork_into(title="Continue with runtime facts")
    fork_inbox = SessionInbox(fork)
    fork_inbox.insert(
        UserMessage(content="Confirm the result."), InboxLane.NEXT_TURN, wake=True
    )
    next_provider, next_boundary = _provider(
        [
            _stream_for_items(
                [_message_item("msg_confirm", "Confirmed.", "final_answer")],
                response_id="resp_confirm",
            )
        ]
    )
    await _run_and_record(
        AgentContext(
            messages=[*restored.history, *fork_inbox.claim_initial("after-restart")],
            state={"validation": validation},
        ),
        next_provider,
        SessionRecorder(fork),
        assembler,
    )
    next_input = next_boundary.requests[0]["input"]
    assert next_input[: len(second_input)] == second_input
    assert next_input[len(second_input)] == final
    assert next_input[-1]["content"][0]["text"] == "Confirm the result."
    assert reopened.current_path.read_bytes() == parent_bytes

    recorder = SessionRecorder(fork)
    recorder.record_context_window_reset(
        window_id="new-window",
        messages_before=len(restored.history),
        messages_after=2,
        replacement=[UserMessage(content="Confirm the result."), contexts[-1]],
    )
    for artifact in artifacts:
        artifact.unlink()
    after_reset = _RestoredHistory()
    replay_session(after_reset, fork)
    assembly = assembler.assemble(
        AgentContext(messages=after_reset.history, state={"validation": validation}),
        current_step=1,
        options=None,
    )
    assembly.require_sendable()
    assert assembly.context_message is None
    assert len([m for m in assembly.messages if isinstance(m, ContextMessage)]) == 1
    assert "Before tool validation" not in str(assembly.wire_messages)


@pytest.mark.asyncio
async def test_agent_session_resume_replays_empty_completed_reasoning(
    replay_workspace: Path,
) -> None:
    empty_reasoning = _reasoning_item()
    empty_reasoning["summary"] = []
    first_stream = _stream_for_items(
        [empty_reasoning],
        response_id="resp_empty_reasoning",
        reasoning_tokens=6,
        output_tokens=24,
    )
    final_item = _message_item(
        "msg_after_reasoning", "The next turn works.", "final_answer"
    )
    second_stream = _stream_for_items(
        [final_item],
        response_id="resp_after_reasoning",
        text_deltas={"msg_after_reasoning": "The next turn works."},
    )
    first_provider, _ = _provider([first_stream])
    sessions_dir = replay_workspace / "sessions"
    store = SessionStore(sessions_dir, project_root=replay_workspace)
    inbox = SessionInbox(store)
    inbox.insert(UserMessage(content="First turn."), InboxLane.NEXT_TURN, wake=True)
    first_user = inbox.claim_initial(run_id="run-empty-reasoning")

    first_result = await _run_and_record(
        AgentContext(messages=first_user), first_provider, SessionRecorder(store)
    )

    assert first_result.termination_reason.value == "completed"
    reopened_store = SessionStore(sessions_dir, project_root=replay_workspace)
    reopened_store.resume(store.current_path)
    restored = _RestoredHistory()
    replay_session(restored, reopened_store)
    empty_assistant = next(
        message
        for message in restored.history
        if isinstance(message, AssistantMessage) and not message.content
    )
    assert empty_assistant.replay_payload is not None
    assert empty_assistant.replay_payload.output_items == (empty_reasoning,)
    assert empty_assistant.replay_payload.reasoning_tokens == 6
    assert empty_assistant.replay_payload.output_tokens == 24
    assert repair_tool_pairing([empty_assistant]) == [empty_assistant]
    assert repair_tool_pairing([AssistantMessage(content=[])]) == []

    reopened_inbox = SessionInbox(reopened_store)
    reopened_inbox.insert(
        UserMessage(content="Second turn."), InboxLane.NEXT_TURN, wake=True
    )
    second_user = reopened_inbox.claim_initial(run_id="run-after-empty-reasoning")
    next_provider, next_boundary = _provider([second_stream])
    next_context = AgentContext(messages=[*restored.history, *second_user])

    await _run_and_record(next_context, next_provider, SessionRecorder(reopened_store))

    assert next_boundary.requests[0]["input"] == [
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "First turn."}],
        },
        empty_reasoning,
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "Second turn."}],
        },
    ]
