from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutionResult:
    output: str = ""
    returncode: int = 0
    timed_out: bool = False
    cancelled: bool = False
    full_output_path: str | None = None
    total_output_lines: int | None = None
