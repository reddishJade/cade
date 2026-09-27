"""Cade Agent — 类型化 Agent 循环。"""

from __future__ import annotations

from .agent import Agent
from .agent_loop import run_agent_loop
from .config import AgentContext, AgentLoopConfig
from .context import (
    ContextAssembler,
    ContextAssemblyInput,
    ContextAssemblyResult,
    ContextBlock,
    ContextBlockSource,
    ContextCollectionInput,
    ContextCollector,
    ContextCollectorRegistry,
    ContextExpiry,
    ContextPriority,
    ContextSection,
    ContextState,
    DefaultContextAssembler,
    FrozenContextCollectorRegistry,
    InstructionCollector,
    InstructionSource,
    NotesCollector,
    RecentValidationCollector,
    WorldState,
    make_collector_section,
    make_state_section,
    trim_to_budget,
)
from .context_manager import (
    ContextManager,
    ContextTokenUsage,
    ContextWindowState,
    PromptCacheMetadata,
)
from .context_policy import ContextPolicy, ContextSnapshot
from .events import AgentEvent
from .messages import (
    AgentMessage,
    AssistantMessage,
    SystemMessage,
    ToolResultMessage,
    UserMessage,
)
from .request import (
    DefaultRequestAssembler,
    RequestAssembler,
    RequestAssembly,
    RequestContextTrace,
    RequestHygiene,
)
from .results import AgentLoopMetrics, AgentLoopResult, TerminationReason
from .types import AgentTool, CancellationSignal

__all__ = [
    "Agent",
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
    "ContextAssemblyInput",
    "ContextAssemblyResult",
    "ContextBlock",
    "ContextBlockSource",
    "ContextCollectionInput",
    "ContextCollector",
    "ContextCollectorRegistry",
    "ContextExpiry",
    "ContextManager",
    "ContextPolicy",
    "ContextPriority",
    "ContextSection",
    "ContextSnapshot",
    "ContextState",
    "ContextTokenUsage",
    "ContextWindowState",
    "DefaultContextAssembler",
    "DefaultRequestAssembler",
    "FrozenContextCollectorRegistry",
    "InstructionCollector",
    "InstructionSource",
    "NotesCollector",
    "PromptCacheMetadata",
    "RecentValidationCollector",
    "RequestAssembler",
    "RequestAssembly",
    "RequestContextTrace",
    "RequestHygiene",
    "SystemMessage",
    "TerminationReason",
    "ToolResultMessage",
    "UserMessage",
    "WorldState",
    "make_collector_section",
    "make_state_section",
    "run_agent_loop",
    "trim_to_budget",
]
