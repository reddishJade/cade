"""智谱 AI ChatGLM provider（兼容 OpenAI Chat API）。"""

from __future__ import annotations

import copy
from typing import Any, cast

from cade.ai.types import StreamOptions, ToolDefinition

from ._codec import to_chat_tools
from ._compat import OpenAICompatProvider

CHATGLM_BASE_URL = "https://open.bigmodel.cn/api/paas/v4/"


class ChatGLMProvider(OpenAICompatProvider):
    """智谱 AI ChatGLM API 适配。"""

    transport = "chatglm_chat"

    @property
    def _clear_thinking(self) -> bool:
        return self.config.clear_thinking

    @property
    def _tool_stream(self) -> bool:
        return self.config.tool_stream

    def _build_chat_params(
        self,
        api_messages: list[dict[str, Any]],
        tools: tuple[ToolDefinition, ...],
        options: StreamOptions | None,
    ) -> dict[str, Any]:

        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "messages": api_messages,
            "tools": to_chat_tools(tools),
            "stream": True,
        }
        if self.config.response_format:
            kwargs["response_format"] = self.config.response_format

        self._build_thinking_params(kwargs, options=options)

        if self._tool_stream and _supports_tool_stream(self.config.model):
            extra_body = cast(dict[str, Any], kwargs.setdefault("extra_body", {}))
            extra_body["tool_stream"] = True
        extra_body = cast(dict[str, Any], kwargs.setdefault("extra_body", {}))
        thinking_body = cast(dict[str, Any], extra_body.setdefault("thinking", {}))
        thinking_body["clear_thinking"] = self._clear_thinking
        return kwargs

    def _clean_reasoning_content(
        self, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        if not messages or not self._clear_thinking:
            return messages
        cleaned = copy.deepcopy(messages)
        for msg in cleaned:
            msg.pop("reasoning_content", None)
        return cleaned


def _supports_tool_stream(model: str) -> bool:
    return model.startswith(("glm-4.6", "glm-4.7"))
