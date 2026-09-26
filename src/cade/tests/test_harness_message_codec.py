"""Provider dict ↔ AgentMessage 编解码单元测试。"""

from __future__ import annotations

from cade.harness.agent_runtime.message_codec import (
    _content_to_text,
    _tool_call_from_provider,
    messages_from_provider_dicts,
)


class TestMessagesFromProviderDicts:
    def test_unknown_role_returns_none(self) -> None:
        result = messages_from_provider_dicts([{"role": "unknown", "content": "x"}])
        assert len(result) == 0


class TestToolCallFromProvider:
    def test_valid(self) -> None:
        item = {"id": "c1", "function": {"name": "search", "arguments": '{"q": "x"}'}}
        result = _tool_call_from_provider(item)
        assert result is not None
        assert result.name == "search"
        assert result.arguments == {"q": "x"}

    def test_dict_args_passthrough(self) -> None:
        item = {"id": "c1", "function": {"name": "search", "arguments": {"q": "x"}}}
        result = _tool_call_from_provider(item)
        assert result is not None
        assert result.arguments == {"q": "x"}

    def test_missing_id_returns_none(self) -> None:
        item = {"function": {"name": "search"}}
        assert _tool_call_from_provider(item) is None

    def test_missing_name_returns_none(self) -> None:
        item = {"id": "c1", "function": {"arguments": "{}"}}
        assert _tool_call_from_provider(item) is None

    def test_not_dict_returns_none(self) -> None:
        assert _tool_call_from_provider("string") is None


class TestContentToText:
    def test_none(self) -> None:
        assert _content_to_text(None) == ""

    def test_list_with_text_blocks(self) -> None:
        content = [{"type": "text", "text": "Hello"}, {"type": "text", "text": "World"}]
        assert _content_to_text(content) == "HelloWorld"

    def test_list_with_tool_result(self) -> None:
        content = [{"type": "tool_result", "content": "output"}]
        assert _content_to_text(content) == "output"


def test_tool_error_metadata_survives_state_round_trip(tmp_path) -> None:
    import json

    from cade.agent.messages import ToolResultMessage
    from cade.harness.agent_runtime.agent_helpers import to_dict

    original = ToolResultMessage(
        tool_call_id="failure-1",
        tool_name="bash",
        content="command failed",
        is_error=True,
        metadata={"exit_code": 7},
        timestamp=123,
    )
    encoded = to_dict(original)
    (tmp_path / "message-state.json").write_text(json.dumps(encoded), encoding="utf-8")
    restored = messages_from_provider_dicts([encoded])[0]
    assert isinstance(restored, ToolResultMessage)
    assert restored.is_error is True
    assert restored.tool_name == "bash"
    assert restored.metadata == {"exit_code": 7}
    assert restored.timestamp == 123


def test_legacy_tool_status_is_restored_as_error() -> None:
    restored = messages_from_provider_dicts(
        [
            {
                "role": "tool",
                "tool_call_id": "failure-1",
                "content": [
                    {
                        "type": "tool_result",
                        "content": "cancelled",
                        "status": "interrupted",
                    }
                ],
            }
        ]
    )[0]
    assert restored.is_error is True
