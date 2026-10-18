"""Cade Agent — 类型化 Agent 循环。"""

from __future__ import annotations

from .agent_loop import run_agent_loop
from .config import AgentContext, AgentLoopConfig
from .context import (
    ContextAssembler,
    ContextAssemblyError,
    ContextAssemblyInput,
    ContextAssemblyResult,
    ContextBlock,
    ContextBlockSource,
    ContextCollectionInput,
    ContextCollector,
    ContextCollectorRegistry,
    ContextPriority,
    ContextSection,
    DefaultContextAssembler,
    FrozenContextCollectorRegistry,
    InstructionCollector,
    InstructionSource,
    NotesCollector,
    make_collector_section,
    make_state_section,
)
from .context_manager import ContextManager
from .context_policy import ContextPolicy, ContextSnapshot
from .events import AgentEvent
from .messages import (
    AgentMessage,
    AssistantMessage,
    ContextMessage,
    SystemMessage,
    ToolResultMessage,
    UserMessage,
)
from .request import (
    DefaultRequestAssembler,
    RequestAssembler,
    RequestAssembly,
    RequestContextTrace,
)
from .results import AgentLoopMetrics, AgentLoopResult, TerminationReason
from .types import AgentTool, CancellationSignal

__all__ = [
    "AgentContext",
    "AgentEvent",
    "AgentLoopConfig",
    "AgentLoopMetrics",
    "AgentLoopResult",
    "AgentMessage",
    "AgentTool",
    "AssistantMessage",
    "CancellationSignal",
    "ContextAssembler",
    "ContextAssemblyError",
    "ContextAssemblyInput",
    "ContextAssemblyResult",
    "ContextBlock",
    "ContextBlockSource",
    "ContextCollectionInput",
    "ContextCollector",
    "ContextCollectorRegistry",
    "ContextManager",
    "ContextMessage",
    "ContextPolicy",
    "ContextPriority",
    "ContextSection",
    "ContextSnapshot",
    "DefaultContextAssembler",
    "DefaultRequestAssembler",
    "FrozenContextCollectorRegistry",
    "InstructionCollector",
    "InstructionSource",
    "NotesCollector",
    "RequestAssembler",
    "RequestAssembly",
    "RequestContextTrace",
    "SystemMessage",
    "TerminationReason",
    "ToolResultMessage",
    "UserMessage",
    "make_collector_section",
    "make_state_section",
    "run_agent_loop",
]
