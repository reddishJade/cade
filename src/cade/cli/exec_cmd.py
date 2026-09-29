"""面向自动化调用方的单次执行协议。"""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, TextIO
from urllib.parse import urlparse

from cade.agent.results import TerminationReason
from cade.agent.types import ApprovalRequest
from cade.coding_agent.execution_modes import ExecutionModeState
from cade.harness.agent_runtime.config import resolve_context_policy
from cade.harness.agent_runtime.events import (
    AgentHarnessEvent,
    ToolResultStructuredEvent,
    ToolUseStructuredEvent,
)
from cade.harness.config import CadeRuntimeConfig, resolve_config_path
from cade.harness.security import HITLResult
from cade.harness.session.schema import SESSION_EVENT_SCHEMA_VERSION
from cade.server.serialize import event_to_dict, to_jsonable

from .session_control import SessionRunControl

_DURATION_RE = re.compile(r"^(?P<value>[0-9]+(?:\.[0-9]+)?)(?P<unit>s|m|h)?$")
_TRANSPORTS = frozenset(
    {
        "openai_chat",
        "openai_responses",
        "openai_codex",
        "chatglm_chat",
        "deepseek_chat",
        "mimo_chat",
        "custom",
    }
)
_EVENT_PREVIEW_CHARS = 4000


class ExecValidationError(ValueError):
    """执行参数或已解析运行时配置不可用于非交互执行。"""


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _duration_seconds(value: str) -> float:
    match = _DURATION_RE.fullmatch(value.strip().lower())
    if match is None:
        raise argparse.ArgumentTypeError("use a duration such as 30s, 45m, or 2h")
    amount = float(match.group("value"))
    if amount <= 0:
        raise argparse.ArgumentTypeError("duration must be positive")
    multiplier = {"s": 1.0, "m": 60.0, "h": 3600.0}.get(match.group("unit") or "s", 1.0)
    return amount * multiplier


def _transport(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_")
    if normalized not in _TRANSPORTS:
        allowed = ", ".join(sorted(item.replace("_", "-") for item in _TRANSPORTS))
        raise argparse.ArgumentTypeError(f"unknown transport; use one of: {allowed}")
    return normalized


def add_exec_arguments(parser: argparse.ArgumentParser) -> None:
    """注册不依赖临时配置文件的 exec 参数。"""
    prompt_group = parser.add_mutually_exclusive_group(required=True)
    prompt_group.add_argument("prompt", nargs="?", help="Prompt text to execute.")
    prompt_group.add_argument(
        "--prompt-file",
        type=Path,
        help="Read the prompt from a UTF-8 file, or use '-' for stdin.",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=argparse.SUPPRESS,
        help="Project root directory.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=argparse.SUPPRESS,
        help="Runtime configuration file.",
    )
    parser.add_argument(
        "--sessions-dir",
        type=Path,
        default=argparse.SUPPRESS,
        help="Session transcript directory.",
    )
    parser.add_argument("--model", help="Override the main model for this run.")
    parser.add_argument(
        "--transport", type=_transport, help="Override the main provider transport."
    )
    parser.add_argument(
        "--reasoning-effort", help="Override reasoning effort for this run."
    )
    parser.add_argument("--mode", choices=["plan", "build", "act"])
    parser.add_argument(
        "--approval",
        choices=["auto-review", "never", "deny", "interactive"],
        help="Approval behavior for this non-interactive run.",
    )
    parser.add_argument("--max-steps", type=_positive_int)
    parser.add_argument("--max-llm-calls", type=_positive_int)
    parser.add_argument("--timeout", type=_duration_seconds)
    parser.add_argument(
        "--event-format",
        choices=["jsonl", "text"],
        default="jsonl",
        help="Output protocol (default: jsonl).",
    )
    parser.add_argument(
        "--event-detail",
        choices=["compact", "full"],
        default="compact",
        help="Event detail level (default: compact).",
    )
    parser.add_argument(
        "--output-last-message",
        type=Path,
        help="Write the final assistant answer to this file.",
    )
    resume_group = parser.add_mutually_exclusive_group()
    resume_group.add_argument(
        "--continue",
        action="store_true",
        dest="continue_",
        default=argparse.SUPPRESS,
    )
    resume_group.add_argument(
        "--session",
        default=argparse.SUPPRESS,
        help="Resume a specific session id.",
    )


def prepare_exec_config(
    runtime_config: CadeRuntimeConfig, args: argparse.Namespace
) -> CadeRuntimeConfig:
    """仅覆盖本次执行需要的配置，不修改磁盘配置。"""
    agent_updates: dict[str, object] = {}
    if args.max_steps is not None:
        agent_updates["max_steps"] = args.max_steps
    if args.max_llm_calls is not None:
        agent_updates["max_llm_calls"] = args.max_llm_calls
    agent = runtime_config.agent.model_copy(update=agent_updates)

    mode_updates: dict[str, object] = {}
    if args.mode is not None:
        mode_updates["default_mode"] = args.mode
    execution_modes = runtime_config.execution_modes.model_copy(update=mode_updates)

    security_updates: dict[str, object] = {}
    if args.approval == "auto-review":
        security_updates.update(approval_policy="on-request", approval_router="auto")
    elif args.approval in {"never", "deny"}:
        security_updates["approval_policy"] = "never"
    elif args.approval == "interactive":
        security_updates.update(approval_policy="on-request", approval_router="user")
    security = runtime_config.security.model_copy(update=security_updates)

    return runtime_config.model_copy(
        update={
            "agent": agent,
            "execution_modes": execution_modes,
            "security": security,
        }
    )


def run_exec(
    args: argparse.Namespace,
    runtime_config: CadeRuntimeConfig,
    app_builder: Callable[[Path, CadeRuntimeConfig, Path], Any],
) -> int:
    """执行一个 prompt，并通过稳定的文本或 NDJSON 协议返回结果。"""
    app: Any | None = None
    emitter = _ExecEmitter(args.event_format, args.event_detail)
    redirected = (
        contextlib.redirect_stdout(sys.stderr)
        if args.event_format == "jsonl"
        else contextlib.nullcontext()
    )
    with redirected:
        try:
            prompt = _read_prompt(args)
            config = prepare_exec_config(runtime_config, args)
            _validate_approval(config, args)
            sessions_dir = (
                args.sessions_dir
                or resolve_config_path(args.project_root, config.paths.sessions_dir)
                or (args.project_root / ".cade" / "sessions")
            )
            app = app_builder(args.project_root, config, sessions_dir)
            if app is None:
                raise RuntimeError("application builder returned no application")
            _configure_model(app, config, args)
            _validate_model_endpoint(app.get_model_info())
            _restore_exec_session(app, args)
            _install_exec_approval(app, config)
            return _consume_exec_run(app, prompt, config, args, emitter)
        except KeyboardInterrupt:
            emitter.completed(status="cancelled", exit_code=130)
            return 130
        except (ExecValidationError, OSError, UnicodeError, ValueError) as exc:
            emitter.completed(
                status="validation_error",
                exit_code=6,
                error={"type": type(exc).__name__, "message": str(exc)},
            )
            return 6
        except RuntimeError as exc:
            emitter.completed(
                status="provider_error",
                exit_code=3,
                error={"type": type(exc).__name__, "message": str(exc)},
            )
            return 3
        finally:
            if app is not None:
                close = getattr(app, "close", None)
                if callable(close):
                    close()


def _read_prompt(args: argparse.Namespace) -> str:
    if args.prompt_file is None:
        prompt = args.prompt or ""
    elif str(args.prompt_file) == "-":
        prompt = sys.stdin.read()
    else:
        prompt = args.prompt_file.read_text(encoding="utf-8")
    if not prompt.strip():
        raise ExecValidationError("prompt must not be empty")
    return prompt


def _validate_approval(config: CadeRuntimeConfig, args: argparse.Namespace) -> None:
    if args.approval == "interactive" and not sys.stdin.isatty():
        raise ExecValidationError("interactive approval requires a TTY")
    security = config.security
    if (
        security.approval_policy == "on-request"
        and _exec_reviewer(config) == "user"
        and not sys.stdin.isatty()
    ):
        raise ExecValidationError(
            "resolved approval policy requires a TTY; use --approval auto-review, "
            "--approval never, or --approval deny"
        )


def _exec_reviewer(config: CadeRuntimeConfig) -> str:
    """沿用执行模式的 reviewer 选择，避免 CLI 维护另一套路由规则。"""
    return ExecutionModeState(
        initial_mode=config.execution_modes.default_mode,
        approval_router=config.security.approval_router,
    ).approvals_reviewer


def _install_exec_approval(app: Any, config: CadeRuntimeConfig) -> None:
    """仅在当前 exec 需要人工审批时安装终端回调。"""
    if (
        config.security.approval_policy == "on-request"
        and _exec_reviewer(config) == "user"
    ):
        app.agent.user_approval_callback = _ExecApprovalHandler()


@contextlib.contextmanager
def _approval_terminal() -> Iterator[tuple[TextIO, TextIO]]:
    """优先使用控制终端，避免 JSONL 标准输出和重定向错误流吞掉提示。"""
    with contextlib.ExitStack() as stack:
        try:
            terminal = stack.enter_context(
                Path("/dev/tty").open("r+", encoding="utf-8")
            )
        except OSError:
            yield sys.stdin, sys.stderr
        else:
            yield terminal, terminal


class _ExecApprovalHandler:
    """把一次权限请求转换为简短的终端批准或拒绝。"""

    def __call__(self, request: ApprovalRequest) -> HITLResult:
        with _approval_terminal() as (reader, writer):
            action = json.dumps(request.action_input, ensure_ascii=False, indent=2)
            if len(action) > 4000:
                action = action[:4000] + "\n... (truncated)"
            writer.write(
                f"\nCade approval ({request.execution_mode or 'unknown mode'})\n"
                f"Tool: {request.tool.name}\n"
                f"Reason: {request.reason}\n"
                f"Directory: {request.working_directory or '(current)'}\n"
                f"Arguments: {action}\n"
                "Approve once? [y/N] "
            )
            writer.flush()
            answer = reader.readline().strip().casefold()
        if answer in {"y", "yes"} and "once" in request.allowed_scopes:
            return HITLResult("allow", "once")
        return HITLResult("deny", "once")


def _configure_model(
    app: Any, config: CadeRuntimeConfig, args: argparse.Namespace
) -> None:
    if not any((args.model, args.transport, args.reasoning_effort)):
        return
    main = config.provider.model_profiles["main"]
    app.set_model(
        model=args.model or main.chat_model,
        transport=args.transport,
        reasoning_effort=args.reasoning_effort,
    )


def _validate_model_endpoint(model_info: dict[str, str]) -> None:
    if model_info.get("transport") != "openai_codex":
        return
    hostname = (urlparse(model_info.get("base_url", "")).hostname or "").lower()
    if hostname != "chatgpt.com" and not hostname.endswith(".chatgpt.com"):
        raise ExecValidationError(
            "openai_codex transport requires a chatgpt.com endpoint"
        )


def _restore_exec_session(app: Any, args: argparse.Namespace) -> None:
    store = app.session_store
    selected = None
    if args.session is not None:
        selected = store.find_by_id(args.session)
        if selected is None:
            raise ExecValidationError(f"session not found: {args.session}")
        stored = (
            Path(selected.project_path).resolve() if selected.project_path else None
        )
        if stored is None or stored != args.project_root.resolve():
            raise ExecValidationError(
                f"session belongs to another project: {selected.project_path}"
            )
    elif args.continue_:
        selected = store.find_latest_for_project(args.project_root)
    if selected is not None:
        store.resume(selected.id)
        app.restore_session()


def _consume_exec_run(
    app: Any,
    prompt: str,
    config: CadeRuntimeConfig,
    args: argparse.Namespace,
    emitter: _ExecEmitter,
) -> int:
    session_id = app.session_store.session_id
    session_path = str(app.session_store.current_path)
    emitter.emit(
        {
            "type": "run.started",
            "run_id": session_id,
            "session_id": session_id,
            "session_path": session_path,
        }
    )
    resolved_config = _resolved_config_event(app, config, args)
    token_budget = resolved_config["token_budget"]
    emitter.token_budget = token_budget if isinstance(token_budget, int) else 0
    emitter.emit(resolved_config)

    timeout_state = _TimeoutState(app, args.timeout)
    run_control = SessionRunControl(Path(session_path).parent, session_id, app)
    workspace_before = _workspace_snapshot(args.project_root)
    run_control.start()
    timeout_state.start()
    final_data: Any | None = None
    approval_denied = False
    validation = _ValidationTracker()
    try:
        for event in app.ask_stream(prompt):
            if event.type == "final":
                final_data = event.data
                continue
            validation.observe(event)
            emitter.emit_event(event)
            if _is_permission_denial(event):
                approval_denied = True
                if args.approval == "deny":
                    app.agent.interrupt("approval denied")
                    break
    finally:
        timeout_state.stop()
        run_control.stop()

    if approval_denied:
        exit_code = 5
        status = "approval_denied"
    elif timeout_state.timed_out:
        exit_code = 124
        status = "timed_out"
    elif final_data is None:
        exit_code = 3
        status = "provider_error"
    else:
        exit_code = _exit_code(final_data)
        status = final_data.termination_reason.value

    answer = "" if final_data is None else final_data.answer
    if args.output_last_message is not None:
        args.output_last_message.parent.mkdir(parents=True, exist_ok=True)
        args.output_last_message.write_text(answer, encoding="utf-8")

    completion = _completion_payload(
        final_data,
        status=status,
        exit_code=exit_code,
        session_id=session_id,
        changed_files=_changed_files(args.project_root, workspace_before),
        validation=validation.results(),
    )
    _record_exec_result(app, completion)
    emitter.completed(**completion)
    return exit_code


def _record_exec_result(app: Any, completion: dict[str, object]) -> None:
    payload = {
        "schema_version": SESSION_EVENT_SCHEMA_VERSION,
        "type": "exec_result",
        "step": completion["steps"],
        "data": to_jsonable(completion),
        "correlation": {},
    }
    try:
        app.session_store.append("event", payload)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        print(f"Failed to persist exec result: {exc}", file=sys.stderr)


def _resolved_config_event(
    app: Any, config: CadeRuntimeConfig, args: argparse.Namespace
) -> dict[str, object]:
    info = app.get_model_info()
    provider = app.agent.provider
    active = getattr(provider, "active_provider", provider)
    policy = resolve_context_policy(active, config.agent)
    context_window = policy.physical_window
    token_budget = policy.input_budget
    reviewer = (
        "configured:reviewer"
        if "reviewer" in config.provider.model_profiles
        else "inherited:main"
    )
    return {
        "type": "config.resolved",
        **info,
        "base_url_origin": (
            "cli_override"
            if args.model is not None or args.transport is not None
            else "resolved_config"
        ),
        "mode": config.execution_modes.default_mode,
        "approval_policy": config.security.approval_policy,
        "approval_router": config.security.approval_router,
        "reviewer_profile": reviewer,
        "context_window": context_window,
        "token_budget": token_budget,
        "output_reserve": policy.output_reserve,
        "operational_headroom": policy.headroom,
        "next_input_allowance": policy.next_input_allowance,
        "admission_target": policy.admission_target,
        "config_precedence": [
            "cli_override",
            "selected_profile",
            "project_config",
            "user_config",
            "model_metadata",
            "built_in_defaults",
        ],
    }


def _is_permission_denial(event: Any) -> bool:
    return bool(
        event.type == "tool_result"
        and event.data.status == "error"
        and event.data.approval_denied
    )


class _ValidationTracker:
    """仅汇总显式标记的验证调用及实际工具结果。"""

    def __init__(self) -> None:
        self._checks: dict[str, dict[str, object]] = {}

    def observe(self, event: AgentHarnessEvent) -> None:
        if isinstance(event, ToolUseStructuredEvent):
            call = event.data
            if call.name == "bash" and call.input.get("purpose") == "validation":
                self._checks[call.id] = {
                    "tool_call_id": call.id,
                    "status": "incomplete",
                    "exit_code": None,
                }
            return
        if not isinstance(event, ToolResultStructuredEvent):
            return
        check = self._checks.get(event.data.tool_use_id)
        if check is None:
            return
        exit_code = event.data.exit_code
        if event.data.approval_denied:
            status = "blocked"
        elif event.data.status == "error" or (exit_code is not None and exit_code != 0):
            status = "failed"
        elif exit_code == 0:
            status = "passed"
        else:
            status = "unknown"
        check["status"] = status
        check["exit_code"] = exit_code

    def results(self) -> list[dict[str, object]]:
        return [dict(check) for check in self._checks.values()]


def _exit_code(final_data: Any) -> int:
    reason = final_data.termination_reason
    if reason is TerminationReason.COMPLETED:
        return 0
    if reason in {
        TerminationReason.STEP_LIMIT,
        TerminationReason.LLM_CALL_LIMIT,
        TerminationReason.WATCHDOG,
    }:
        return 2
    if reason is TerminationReason.CANCELLED:
        return 130
    failure = final_data.provider_failure
    if failure is not None and (
        failure.status_code == 413
        or failure.exception_type == "RequestBudgetExceededError"
    ):
        return 4
    return 3


def _completion_payload(
    final_data: Any | None,
    *,
    status: str,
    exit_code: int,
    session_id: str,
    changed_files: list[str],
    validation: list[dict[str, object]],
) -> dict[str, object]:
    metrics = final_data.metrics if final_data is not None else None
    failure = final_data.provider_failure if final_data is not None else None
    error: dict[str, object] | None = None
    if failure is not None:
        error = {
            "type": "usage_limit"
            if failure.status_code == 429
            else failure.exception_type,
            "message": failure.message,
            "status_code": failure.status_code,
        }
    elif final_data is not None and final_data.error_detail:
        error = {"type": status, "message": final_data.error_detail}
    elif status == "approval_denied":
        error = {
            "type": "approval_denied",
            "message": "a tool action required approval and was denied",
        }
    return {
        "status": status,
        "exit_code": exit_code,
        "termination_reason": (
            final_data.termination_reason.value if final_data is not None else status
        ),
        "session_id": session_id,
        "steps": 0 if final_data is None else final_data.steps,
        "llm_calls": 0 if metrics is None else metrics.get("llm_calls", 0),
        "tool_calls": 0 if metrics is None else metrics.get("tool_calls", 0),
        "changed_files": changed_files,
        "validation": validation,
        "error": error,
        "answer": "" if final_data is None else final_data.answer,
    }


type _FileSignature = tuple[int, int] | None


def _workspace_snapshot(project_root: Path) -> dict[str, _FileSignature] | None:
    """用路径和文件元数据记录 Git 工作树，不计算内容摘要。"""
    try:
        completed = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=project_root,
            check=False,
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    paths = (
        item.decode("utf-8", errors="replace")
        for item in completed.stdout.split(b"\0")
        if item
    )
    return {path: _file_signature(project_root / path) for path in paths}


def _file_signature(path: Path) -> _FileSignature:
    try:
        stat = path.lstat()
    except OSError:
        return None
    return stat.st_size, stat.st_mtime_ns


def _changed_files(
    project_root: Path,
    before: dict[str, _FileSignature] | None,
) -> list[str]:
    if before is None:
        return []
    after = _workspace_snapshot(project_root)
    if after is None:
        return []
    return sorted(
        path
        for path in before.keys() | after.keys()
        if not _is_cade_internal_path(path) and before.get(path) != after.get(path)
    )


def _is_cade_internal_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized == ".cade" or normalized.startswith(".cade/")


class _TimeoutState:
    """在墙钟超时后请求中断活动 run。"""

    def __init__(self, app: Any, timeout: float | None) -> None:
        self._app = app
        self._timeout = timeout
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.timed_out = False

    def start(self) -> None:
        if self._timeout is None:
            return
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.2)

    def _watch(self) -> None:
        if self._stop.wait(self._timeout):
            return
        self.timed_out = True
        while not self._stop.is_set():
            if self._app.agent.interrupt("exec timeout reached"):
                return
            time.sleep(0.01)


class _ExecEmitter:
    """保证 JSONL stdout 只包含完整 JSON 对象。"""

    def __init__(self, event_format: str, event_detail: str = "compact") -> None:
        self._event_format = event_format
        self._event_detail = event_detail
        self._output = sys.stdout
        self.token_budget = 0

    def emit(self, payload: dict[str, object]) -> None:
        if self._event_format == "jsonl":
            print(
                json.dumps(
                    to_jsonable(payload), ensure_ascii=False, separators=(",", ":")
                ),
                file=self._output,
                flush=True,
            )
            return
        event_type = payload.get("type", "event")
        print(
            f"[{event_type}] {json.dumps(to_jsonable(payload), ensure_ascii=False)}",
            file=self._output,
        )

    def emit_event(self, event: Any) -> None:
        payload = event_to_dict(event)
        raw_step = payload.get("step", 0)
        step = raw_step if isinstance(raw_step, int) else 0
        payload["step"] = step
        event_type = payload.get("type")
        data = payload.get("data")
        if self._event_detail == "full":
            self.emit(payload)
            return
        if event_type == "message_start":
            self.emit({"type": "step.started", "step": step})
        elif event_type == "tool_use" and isinstance(data, dict):
            self.emit(
                {
                    "type": "tool.started",
                    "step": step,
                    "name": data.get("name"),
                    "tool_call_id": data.get("id"),
                }
            )
        elif event_type == "tool_result" and isinstance(data, dict):
            self.emit(
                {
                    "type": "tool.completed",
                    "step": step,
                    "tool_call_id": data.get("tool_use_id"),
                    "status": data.get("status"),
                    "exit_code": data.get("exit_code"),
                    "approval_denied": data.get("approval_denied"),
                    **_content_preview(data.get("content")),
                    "permission_notice": data.get("permission_notice"),
                }
            )
        elif event_type == "context_window_reset" and isinstance(data, dict):
            self.emit(
                {
                    "type": "context.reset",
                    "step": step,
                    "reason": data.get("reason"),
                    "window_index": data.get("window_index"),
                }
            )
        elif event_type == "usage_update" and isinstance(data, dict):
            self.emit(
                {
                    "type": "budget.updated",
                    "step": step,
                    "estimated_tokens": data.get("input_tokens", 0),
                    "output_tokens": data.get("output_tokens", 0),
                    "token_budget": self.token_budget,
                }
            )
        elif event_type in {"error", "warning"}:
            self.emit(payload)

    def completed(self, **payload: object) -> None:
        self.emit({"type": "run.completed", **payload})


def _content_preview(value: object) -> dict[str, object]:
    """为机器事件提供有界预览，完整正文仍保存在 session 中。"""
    if value is None:
        return {"content": None, "content_chars": 0, "content_truncated": False}
    content = value if isinstance(value, str) else json.dumps(to_jsonable(value))
    length = len(content)
    return {
        "content": content[:_EVENT_PREVIEW_CHARS],
        "content_chars": length,
        "content_truncated": length > _EVENT_PREVIEW_CHARS,
    }
