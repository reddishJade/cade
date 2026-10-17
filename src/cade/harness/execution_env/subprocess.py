from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from io import BufferedReader
from pathlib import Path
from typing import cast

from ._process import POLL_INTERVAL, close_pipes, kill_process, start_process
from .output_capture import OutputCapture
from .result import ExecutionResult
from .sandbox import CommandSandbox

logger = logging.getLogger(__name__)


class SubprocessShell:
    def __init__(self, sandbox: CommandSandbox | None = None) -> None:
        self._sandbox = sandbox

    def run(
        self,
        argv: list[str],
        cwd: Path,
        timeout: int = 30_000,
        cancel_event: threading.Event | None = None,
        on_progress: Callable[[str], None] | None = None,
        env: dict[str, str] | None = None,
    ) -> ExecutionResult:
        finalize_sandbox: Callable[[], str | None] | None = None
        if self._sandbox is not None:
            command = self._sandbox.wrap(argv, cwd)
            argv = list(command.argv)
            cwd = command.cwd
            finalize_sandbox = command.finalize
        try:
            proc = start_process(argv, cwd, env=env)
        except OSError:
            if finalize_sandbox is not None:
                finalize_sandbox()
            raise
        output_capture = OutputCapture()
        cancelled = False
        timed_out = False
        sandbox_violation: str | None = None

        def _drain() -> None:
            if proc.stdout is None:
                return
            try:
                stdout = cast(BufferedReader, proc.stdout)
                while raw := stdout.read1(8192):
                    progress = output_capture.append(raw)
                    if on_progress and progress:
                        on_progress(progress)
            except (LookupError, OSError, RuntimeError, TypeError, ValueError):
                logger.debug("error draining process output", exc_info=True)

        output_thread = threading.Thread(target=_drain, daemon=True)
        output_thread.start()

        try:
            deadline = time.monotonic() + (timeout / 1000.0)
            while proc.poll() is None:
                if time.monotonic() >= deadline:
                    kill_process(proc)
                    timed_out = True
                    break
                if cancel_event is not None and cancel_event.is_set():
                    kill_process(proc)
                    cancelled = True
                    break
                time.sleep(POLL_INTERVAL)
            output_thread.join(timeout=2)
        except Exception:
            kill_process(proc)
            raise
        finally:
            close_pipes(proc)
            if finalize_sandbox is not None:
                sandbox_violation = finalize_sandbox()

        output, full_output_path, total_lines = output_capture.finish()
        returncode = proc.returncode
        if sandbox_violation is not None:
            if on_progress is not None:
                on_progress(f"\n{sandbox_violation}\n")
            if output and not output.endswith("\n"):
                output += "\n"
            output += sandbox_violation
            if returncode == 0:
                returncode = 126

        return ExecutionResult(
            output=output,
            returncode=returncode,
            timed_out=timed_out,
            cancelled=cancelled,
            full_output_path=full_output_path,
            total_output_lines=total_lines,
        )
