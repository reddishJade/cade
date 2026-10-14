"""通用运行事件与 harness 最终结果。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from cade.agent.events import AgentEvent, EventCorrelation

if TYPE_CHECKING:
    from .result import AgentHarnessResult


@dataclass(frozen=True)
class FinalEvent:
    type: Literal["final"]
    step: int
    data: AgentHarnessResult
    correlation: EventCorrelation = field(default_factory=EventCorrelation)


type AgentHarnessEvent = AgentEvent | FinalEvent
