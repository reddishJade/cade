"""Agent 消息类型。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from cade.agent.types import (
    ContentBlock,
    FileContent,
    ImageContent,
    ShellCallOutputContent,
    TextContent,
    ToolRenderIntent,
    ToolResultContent,
)
from cade.ai.events import ProviderFailure, ProviderReplayPayload, StopReason

type UserContent = str | list[TextContent | ImageContent | FileContent]
type ToolResultMessageContent = (
    str
    | list[
        TextContent
        | ImageContent
        | FileContent
        | ToolResultContent
        | ShellCallOutputContent
    ]
)

# ── 消息类型 ──


class SystemMessage(BaseModel):
    role: str = "system"
    content: str = ""
    timestamp: int = 0
    model_config = ConfigDict(extra="forbid")


class UserMessage(BaseModel):
    role: str = "user"
    content: UserContent = ""
    # 会话来源与模型角色不同；旧记录默认不可信。
    source: Literal["user", "runtime", "subagent", "unknown"] = "unknown"
    timestamp: int = 0
    model_config = ConfigDict(extra="forbid")


class ContextMessage(BaseModel):
    """完整运行时事实快照；新快照替代旧状态，不代表用户意图。"""

    role: Literal["context"] = "context"
    content: str = ""
    timestamp: int = 0
    model_config = ConfigDict(extra="forbid", frozen=True)


class AssistantMessage(BaseModel):
    role: str = "assistant"
    content: list[ContentBlock] = Field(default_factory=list)
    reasoning_content: str | None = None
    phase: str | None = None
    stop_reason: StopReason = "end_turn"
    error_message: str | None = None
    provider_failure: ProviderFailure | None = None
    replay_payload: ProviderReplayPayload | None = None
    model: str = ""
    provider: str = ""
    timestamp: int = 0
    usage: dict[str, int] | None = None
    model_config = ConfigDict(extra="forbid")


class ToolResultMessage(BaseModel):
    role: str = "tool_result"
    tool_call_id: str = ""
    tool_name: str = ""
    content: ToolResultMessageContent = ""
    is_error: bool = False
    metadata: dict[str, object] | None = None
    render_intent: ToolRenderIntent | None = None
    timestamp: int = 0
    model_config = ConfigDict(extra="forbid")


class BranchSummaryMessage(BaseModel):
    role: str = "branch_summary"
    summary: str = ""
    from_id: str = ""
    timestamp: int = 0
    model_config = ConfigDict(extra="forbid")


type AgentMessage = (
    SystemMessage
    | UserMessage
    | ContextMessage
    | AssistantMessage
    | ToolResultMessage
    | BranchSummaryMessage
)
