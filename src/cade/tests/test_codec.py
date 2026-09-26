"""消息与工具编码单元测试。"""

from __future__ import annotations

from cade.ai.providers._codec import (
    canonical_tool_schema,
    make_schema_strict,
    normalize_cross_provider_messages,
    provider_function_name,
    to_chat_messages,
    to_chat_tool,
    to_chat_tools,
    tool_catalog_fingerprint,
)
from cade.ai.types import ToolDefinition


class TestMakeSchemaStrict:
    def test_simple_object(self) -> None:
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        }
        result = make_schema_strict(schema)
        assert result["additionalProperties"] is False
        assert result["required"] == ["name"]

    def test_nulls_optional_properties(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"},
            },
            "required": ["name"],
        }
        result = make_schema_strict(schema)
        # name is required → stays as is
        assert result["properties"]["name"]["type"] == "string"
        # age is not required → becomes nullable union
        assert result["properties"]["age"]["type"] == ["integer", "null"]

    def test_removes_unsupported_keys(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "x": {"type": "string", "default": "hello", "format": "email"}
            },
            "required": ["x"],
        }
        result = make_schema_strict(schema)
        prop = result["properties"]["x"]
        assert "default" not in prop
        assert "format" not in prop

    def test_nested_objects(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "meta": {
                    "type": "object",
                    "properties": {"key": {"type": "string"}},
                    "required": ["key"],
                }
            },
            "required": ["meta"],
        }
        result = make_schema_strict(schema)
        assert result["properties"]["meta"]["additionalProperties"] is False


class TestNormalizeCrossProviderMessages:
    def test_no_reasoning_content_noop(self) -> None:
        msgs = [{"role": "user", "content": "hello"}]
        assert normalize_cross_provider_messages(msgs, "openai_chat") == msgs

    def test_target_supports_reasoning_noop(self) -> None:
        msgs = [{"role": "assistant", "content": "hi", "reasoning_content": "think"}]
        result = normalize_cross_provider_messages(msgs, "deepseek_chat")
        assert "reasoning_content" in result[0]

    def test_converts_reasoning_for_unsupported(self) -> None:
        msgs = [
            {"role": "assistant", "content": "hi", "reasoning_content": "think step"}
        ]
        result = normalize_cross_provider_messages(msgs, "openai_chat")
        assert "reasoning_content" not in result[0]
        assert "<thinking>think step</thinking>" in result[0]["content"]

    def test_reasoning_without_content_inserts_thinking_block(self) -> None:
        msgs = [{"role": "assistant", "reasoning_content": "deep thoughts"}]
        result = normalize_cross_provider_messages(msgs, "openai_chat")
        assert result[0]["content"] == "<thinking>deep thoughts</thinking>"


class TestToChatMessages:
    def test_tool_result_null_content(self) -> None:
        result = to_chat_messages(
            [{"role": "tool", "tool_call_id": "call1", "content": None}]
        )
        assert result == [{"role": "tool", "tool_call_id": "call1", "content": ""}]

    def test_tool_calls_normalized(self) -> None:
        msgs = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call1",
                        "function": {
                            "name": "get_weather",
                            "arguments": {"city": "Beijing"},
                        },
                    }
                ],
            }
        ]
        result = to_chat_messages(msgs)
        tc = result[0]["tool_calls"][0]
        assert isinstance(tc["function"]["arguments"], str)
        assert "Beijing" in tc["function"]["arguments"]

    def test_content_blocks_with_tool_use(self) -> None:
        msgs = [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Let me check"},
                    {
                        "type": "tool_use",
                        "id": "tu1",
                        "name": "search",
                        "input": {"q": "x"},
                    },
                ],
            }
        ]
        result = to_chat_messages(msgs)
        assert len(result) == 1
        assert result[0]["role"] == "assistant"
        assert result[0]["content"] == "Let me check"
        assert len(result[0]["tool_calls"]) == 1

    def test_content_blocks_with_tool_result(self) -> None:
        msgs = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tu1",
                        "content": "result data",
                    },
                ],
            }
        ]
        result = to_chat_messages(msgs)
        assert result[0]["role"] == "tool"
        assert result[0]["tool_call_id"] == "tu1"


class TestToChatTool:
    def test_tool_without_schema_creates_default(self) -> None:
        result = to_chat_tool("test", "A test", None)
        assert "input" in result["function"]["parameters"]["properties"]

    def test_strict_mode(self) -> None:
        result = to_chat_tool(
            "test",
            "A test",
            {"type": "object", "properties": {"x": {"type": "string"}}},
            strict=True,
        )
        assert result["function"]["strict"] is True


class TestToChatTools:
    def test_strict_multiple_tools(self) -> None:
        tools = [
            ToolDefinition(
                name="a", description="first", parameters={"type": "object"}
            ),
        ]
        result = to_chat_tools(tuple(tools), strict=True)
        assert result[0]["function"]["strict"] is True


class TestCanonicalToolSchema:
    def test_includes_builtin(self) -> None:
        tool = ToolDefinition(
            name="t", description="d", parameters={}, builtin={"type": "computer"}
        )
        result = canonical_tool_schema(tool)
        assert result["builtin"] == {"type": "computer"}

    def test_fingerprint_stable(self) -> None:
        tools = [
            ToolDefinition(name="b", description="second", parameters={}),
            ToolDefinition(name="a", description="first", parameters={}),
        ]
        fp1 = tool_catalog_fingerprint(tools)
        fp2 = tool_catalog_fingerprint(list(reversed(tools)))
        assert fp1 == fp2


class TestProviderFunctionName:
    def test_uri_id(self) -> None:
        result = provider_function_name("filesystem://read_file")
        assert "filesystem" in result
        assert "read_file" in result
        assert "://" not in result
