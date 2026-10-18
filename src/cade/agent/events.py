"""Agent 事件类型。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .context_policy import ContextPolicy
from .messages import AgentMessage, ToolResultMessage
from .types import AgentToolResult, ToolArguments


class TurnStartEvent(BaseModel):
    """单次 turn 开始事件（调用模型前）。"""

    type: str = "turn_start"
    model_config = ConfigDict(extra="forbid")


class TurnEndEvent(BaseModel):
    """单次 turn 结束事件（模型返回并执行工具后）。"""

    type: str = "turn_end"
    message: AgentMessage | None = None
    tool_results: list[ToolResultMessage] = Field(default_factory=list)
    model_config = ConfigDict(extra="forbid")


class MessageStartEvent(BaseModel):
    """模型响应开始事件（首个 token 到达）。"""

    type: str = "message_start"
    message: AgentMessage | None = None
    model_config = ConfigDict(extra="forbid")


class MessageUpdateEvent(BaseModel):
    """模型响应增量更新事件（流式传输中）。"""

    type: str = "message_update"
    message: AgentMessage | None = None
    model_config = ConfigDict(extra="forbid")


class MessageEndEvent(BaseModel):
    """模型响应结束事件（流式传输完成）。"""

    type: str = "message_end"
    message: AgentMessage | None = None
    model_config = ConfigDict(extra="forbid")


class ToolExecutionStartEvent(BaseModel):
    """工具执行开始事件。"""

    type: str = "tool_execution_start"
    tool_call_id: str = ""
    tool_name: str = ""
    args: ToolArguments = Field(default_factory=dict)
    model_config = ConfigDict(extra="forbid")


class ToolExecutionUpdateEvent(BaseModel):
    """工具执行增量更新事件（支持进度报告的工具）。"""

    type: str = "tool_execution_update"
    tool_call_id: str = ""
    tool_name: str = ""
    args: ToolArguments = Field(default_factory=dict)
    partial_result: AgentToolResult | None = None
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)


class ToolExecutionEndEvent(BaseModel):
    """工具执行结束事件，携带结果或错误。"""

    type: str = "tool_execution_end"
    tool_call_id: str = ""
    tool_name: str = ""
    result: ToolResultMessage | None = None
    is_error: bool = False
    model_config = ConfigDict(extra="forbid")


class ThinkingUpdateEvent(BaseModel):
    """思考内容增量更新事件（支持 reasoning_content 的模型）。"""

    type: str = "thinking_update"
    reasoning_content: str = ""
    model_config = ConfigDict(extra="forbid")


class UsageUpdateEvent(BaseModel):
    """Provider 返回的当次请求 token 用量。"""

    type: str = "usage_update"
    input_tokens: int = 0
    output_tokens: int = 0
    model_config = ConfigDict(extra="forbid")


class ResponseMetricsEvent(BaseModel):
    """单次模型请求的性能；首增量时更新 TTFT，流结束时更新 TPS。"""

    type: str = "response_metrics"
    ttft_seconds: float | None = None
    tokens_per_second: float | None = None
    completed: bool = False
    model_config = ConfigDict(extra="forbid")


class ContextWindowResetEvent(BaseModel):
    """旧上下文窗口关闭、新窗口开始事件。"""

    type: str = "context_window_reset"
    window_id: str = ""
    messages_removed: int = 0
    messages_after: int = 0
    trigger: str = "token_limit"
    replacement: list[AgentMessage]
    reproject_from_facts: bool = False
    rollover_policy: ContextPolicy | None = None
    preserve_user_request: bool = True
    model_config = ConfigDict(extra="forbid")


type AgentEvent = (
    TurnStartEvent
    | TurnEndEvent
    | MessageStartEvent
    | MessageUpdateEvent
    | MessageEndEvent
    | ToolExecutionStartEvent
    | ToolExecutionUpdateEvent
    | ToolExecutionEndEvent
    | ThinkingUpdateEvent
    | UsageUpdateEvent
    | ResponseMetricsEvent
    | ContextWindowResetEvent
)
