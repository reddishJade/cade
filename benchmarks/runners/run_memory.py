"""三臂记忆经验评测：none / relevant / irrelevant。

每个任务都在隔离工作区中运行三次：不注入 `MEMORY.md`、注入与 fixture 故障
相关的经验记录、注入锚定在另一个文件上的无关诱饵加一条陈旧记录。种子文件在
确定性初始提交之前写入，因此基线工作区始终干净，`git diff HEAD` 只反映本次
会话的改动。

新鲜度来自内容快照而不是 git commit：relevant 臂用 `{anchor_state}` 占位，
运行器按锚点文件的实际内容解析，提示读出 `state=unchanged`；irrelevant 臂的
陈旧记录带一个不匹配的摘要，读出 `state=changed`（记录确实会触发，这正是负
迁移要测的情形）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from benchmarks.evaluators.state_retention import (
    capture_initial_state,
    evaluate_state_retention,
    retention_rate,
)
from benchmarks.evaluators.test_result import run_command
from benchmarks.models import (
    LongHorizonTask,
    MemoryScenarioSpec,
    discover_task_files,
    load_task,
)
from benchmarks.reports.generate_memory_report import write_memory_report
from benchmarks.runners._cli import _runtime_config
from benchmarks.runners._long_horizon import (
    ProviderCallRecord,
    RunOptions,
    _benchmark_runtime_config,
    _build_benchmark_app,
    _prepare_workspace,
    _repeated_read_calls,
    _run_git,
    _run_turn,
    _usage_incomplete_calls,
)
from benchmarks.runners.progress import (
    ProgressStage,
    ProgressUpdate,
    create_progress_reporter,
)
from cade.ai.events import Message
from cade.harness.config import CadeRuntimeConfig
from cade.harness.memory import MemoryManager, anchor_snapshot, select_hints
from cade.harness.memory.experience import parse_experience, render_hint_line
from cade.harness.memory.parsing import parse_memory_blocks
from cade.harness.session import SessionStore

Arm = Literal["none", "relevant", "irrelevant"]

ARMS: tuple[Arm, ...] = ("none", "relevant", "irrelevant")
MEMORY_FILE = Path("MEMORY.md")
_SNAPSHOT_PLACEHOLDER = "{anchor_state}"
_MEMORY_BLOCK_MARKER = "<memory-hints"
_READ_TOOLS = frozenset({"read", "read_file"})
_WRITE_TOOLS = frozenset({"write", "edit"})


def main() -> None:
    args = _parser().parse_args()
    if args.repeat <= 0:
        raise ValueError("--repeat must be positive")
    tasks = [load_task(path) for path in discover_task_files(args.tasks)]
    for task in tasks:
        if task.memory is None:
            raise ValueError(
                f"task {task.id!r} declares no memory scenario: {task.manifest_path}"
            )
    if args.dry_run:
        _dry_run(tasks, args)
        return
    runtime_config = _runtime_config(args.config)
    records = _run_all(tasks, args, runtime_config)
    write_memory_report(records, args.output_dir.resolve())
    print(args.output_dir.resolve() / "report.md")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the three-arm memory experience benchmark."
    )
    parser.add_argument(
        "tasks",
        nargs="*",
        type=Path,
        default=[Path("benchmarks/tasks/memory")],
        help="task.json file or a directory containing memory task manifests",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="explicit Cade runtime config; otherwise discover config from cwd",
    )
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--temperature", type=float)
    parser.add_argument("--keep-workspaces", action="store_true")
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="disable the progress bar and progress log output",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "validate manifests, prepare seeded workspaces, print the resolved "
            "plan, and exit without any provider call"
        ),
    )
    parser.add_argument("--output-dir", type=Path, default=_default_output_dir())
    return parser


def _run_all(
    tasks: list[LongHorizonTask],
    args: argparse.Namespace,
    runtime_config: CadeRuntimeConfig,
) -> list[dict[str, Any]]:
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    total_runs = args.repeat * len(tasks) * len(ARMS)
    reporter = create_progress_reporter(total_runs, enabled=not args.no_progress)
    records: list[dict[str, Any]] = []
    with reporter:
        for repetition in range(1, args.repeat + 1):
            for task in tasks:
                for arm in _ordered_arms(repetition):
                    options = RunOptions(
                        output_dir=output_dir,
                        repeat=repetition,
                        temperature=args.temperature,
                        keep_workspace=args.keep_workspaces,
                        progress_callback=reporter.update,
                    )
                    record = run_memory_attempt(task, arm, runtime_config, options)
                    records.append(record)
                    print(
                        f"{task.id} {arm} r{repetition}: "
                        f"success={record['task_success']} "
                        f"usage_complete={record['usage_complete']} "
                        f"input_tokens={record['input_tokens_total']} "
                        f"hint_fired={record['hint_fired']} "
                        f"steps_to_anchor={record['steps_to_anchor']}",
                        flush=True,
                    )
    return records


def run_memory_attempt(
    task: LongHorizonTask,
    arm: Arm,
    runtime_config: CadeRuntimeConfig,
    options: RunOptions,
) -> dict[str, Any]:
    """在隔离工作区运行一个实验臂，并写出单次原始记录。"""
    spec = task.memory
    if spec is None:
        raise ValueError(f"task {task.id!r} has no memory scenario")
    seed_source = _seed_source(spec, arm)
    current_turn = 0

    def emit_progress(
        stage: ProgressStage,
        detail: str = "",
        *,
        turn: int | None = None,
    ) -> None:
        callback = options.progress_callback
        if callback is None:
            return
        callback(
            ProgressUpdate(
                stage=stage,
                task_id=task.id,
                variant=arm,
                repeat=options.repeat,
                attempt=1,
                total_turns=len(task.turns),
                turn=turn,
                detail=detail,
            )
        )

    run_id = f"{task.id}-{arm}-r{options.repeat}-{uuid4().hex[:8]}"
    output_dir = options.output_dir.resolve()
    workspace = output_dir / "workspaces" / run_id
    if workspace.exists():
        raise ValueError(f"benchmark workspace already exists: {workspace}")
    emit_progress("run_started", f"workspace={workspace.name}")
    workspace.parent.mkdir(parents=True, exist_ok=True)
    baseline_commit = _prepare_workspace(
        task.workspace,
        workspace,
        {MEMORY_FILE: seed_source} if seed_source is not None else None,
        _resolve_memory_snapshot if arm == "relevant" else None,
    )
    initial_worktree_clean = not _run_git(workspace, "status", "--porcelain")
    initial_state = capture_initial_state(workspace, task.state_checks)
    memory_before = _file_digest(workspace / MEMORY_FILE)
    memory_hint_state, _hint_line = _seeded_hint(workspace, spec.anchor_path)

    calls: list[ProviderCallRecord] = []
    hint_fired = False

    def observe(request_messages: list[Message]) -> None:
        nonlocal hint_fired
        if not hint_fired and _request_has_memory_block(request_messages):
            hint_fired = True

    runtime_dir = output_dir / "runtime" / run_id
    configured = _benchmark_runtime_config(
        runtime_config,
        task,
        sessions_dir=runtime_dir / "sessions",
    )
    started_at = datetime.now(UTC)
    started = time.perf_counter()
    app = _build_benchmark_app(
        workspace,
        configured,
        "cade",
        options,
        calls,
        progress_callback=lambda stage, detail: emit_progress(
            stage,
            detail,
            turn=current_turn or None,
        ),
        request_observer=observe,
    )
    turn_records: list[dict[str, object]] = []
    runtime_errors: list[str] = []
    terminations: list[str] = []
    try:
        for turn_index, turn in enumerate(task.turns, 1):
            current_turn = turn_index
            emit_progress("turn_started", turn.prompt, turn=turn_index)
            call_start = len(calls)
            turn_started = time.perf_counter()
            try:
                result, _resets = _run_turn(
                    app,
                    turn.prompt,
                    progress_callback=lambda stage, detail, turn_number=turn_index: (
                        emit_progress(stage, detail, turn=turn_number)
                    ),
                )
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                emit_progress("error", str(exc), turn=turn_index)
                runtime_errors.append(f"turn {turn_index}: {exc}")
                turn_records.append(
                    {
                        "turn": turn_index,
                        "duration_seconds": time.perf_counter() - turn_started,
                        "error": str(exc),
                        "provider_calls": [
                            call.to_dict() for call in calls[call_start:]
                        ],
                        "tool_calls": [],
                    }
                )
                break
            terminations.append(str(result.termination_reason))
            turn_records.append(
                {
                    "turn": turn_index,
                    "duration_seconds": time.perf_counter() - turn_started,
                    "termination_reason": str(result.termination_reason),
                    "provider_calls": [call.to_dict() for call in calls[call_start:]],
                }
            )
            emit_progress(
                "turn_completed",
                f"termination={result.termination_reason}",
                turn=turn_index,
            )
    finally:
        app.close()

    emit_progress("verification", "task success command and state checks")
    verification = run_command(task.success_command, workspace)
    state_outcomes = evaluate_state_retention(
        workspace, task.state_checks, initial_state
    )
    tool_calls = _branch_tool_calls(app.session_store)
    first_write_path = _first_write_path(tool_calls)
    input_tokens = sum(call.input_tokens for call in calls)
    usage_issues = _usage_incomplete_calls(calls)
    memory_after = _file_digest(workspace / MEMORY_FILE)
    record: dict[str, Any] = {
        "schema_version": 1,
        "run_id": run_id,
        "task_id": task.id,
        "arm": arm,
        "repeat": options.repeat,
        "model": app.agent.provider.model,
        "temperature": options.temperature,
        "execution_mode": "build",
        "baseline_commit": baseline_commit,
        "initial_worktree_clean": initial_worktree_clean,
        "memory_seed": seed_source.name if seed_source is not None else None,
        "memory_seed_sha256": memory_before,
        "memory_hint_state": memory_hint_state,
        "memory_file_changed": memory_before != memory_after,
        "started_at": started_at.isoformat(),
        "duration_seconds": time.perf_counter() - started,
        "turns_expected": len(task.turns),
        "turns_completed": len(turn_records) - int(bool(runtime_errors)),
        "termination_reasons": terminations,
        "runtime_errors": runtime_errors,
        "provider_call_count": len(calls),
        "input_tokens_total": input_tokens,
        "output_tokens_total": sum(call.output_tokens for call in calls),
        "peak_input_tokens": max((call.input_tokens for call in calls), default=0),
        "usage_complete": bool(calls) and all(call.has_usage for call in calls),
        "usage_incomplete_calls": usage_issues,
        "task_success": verification.passed,
        "verification": verification.to_dict(),
        "state_retention": retention_rate(state_outcomes),
        "state_checks": [outcome.to_dict() for outcome in state_outcomes],
        "tool_call_count": len(tool_calls),
        "distinct_files_read": _distinct_files_read(tool_calls),
        "repeated_read_calls": _repeated_read_calls(tool_calls),
        # 诊断量：两个 fixture 的任务文本都会点名要修改的文件，因此该步数
        # 在三个臂之间差异有限，不能作为主结果使用。
        "steps_to_anchor": _steps_to_anchor(tool_calls, spec.anchor_path),
        "hint_fired": hint_fired,
        "stale_follow": _stale_follow(tool_calls, spec.stale_anchor_path),
        "stale_hint_acted": _matches_repo_path(first_write_path, spec.stale_fix_path),
        "first_write_path": first_write_path,
        "remember_calls": sum(call["name"] == "remember" for call in tool_calls),
        "tool_calls": tool_calls,
        "turns": turn_records,
    }
    cleanup_errors: list[str] = []
    if options.keep_workspace:
        record["workspace"] = str(workspace)
        record["runtime_dir"] = str(runtime_dir)
    else:
        for target in (workspace, runtime_dir):
            warning = _discard_tree(target)
            if warning is None:
                continue
            cleanup_errors.append(warning)
            print(f"warning: workspace cleanup failed: {warning}", file=sys.stderr)
    record["cleanup_errors"] = cleanup_errors
    output_dir.mkdir(parents=True, exist_ok=True)
    record_path = output_dir / f"{run_id}.json"
    record_path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    record["record_path"] = str(record_path)
    emit_progress(
        "run_completed",
        f"success={verification.passed} input_tokens={input_tokens}",
    )
    return record


def _dry_run(tasks: list[LongHorizonTask], args: argparse.Namespace) -> None:
    """校验清单、预置工作区并打印计划；不进行任何 provider 调用。"""
    output_dir = args.output_dir.resolve()
    print(
        f"dry run: {len(tasks)} task(s) x {len(ARMS)} arms x repeat {args.repeat} "
        f"= {len(tasks) * len(ARMS) * args.repeat} planned runs"
    )
    print(f"output directory: {output_dir}")
    for task in tasks:
        spec = task.memory
        if spec is None:  # main() 已拒绝缺少记忆场景的清单
            raise ValueError(f"task {task.id!r} has no memory scenario")
        print(f"\ntask {task.id}")
        print(f"  manifest: {task.manifest_path}")
        print(f"  fixture: {task.workspace}")
        print(f"  turns: {len(task.turns)}")
        print(f"  success_command: {' '.join(task.success_command.argv)}")
        print(f"  anchor_path: {spec.anchor_path}")
        print(f"  stale_anchor_path: {spec.stale_anchor_path}")
        print(f"  stale_fix_path: {spec.stale_fix_path}")
        for arm in ARMS:
            seed_source = _seed_source(spec, arm)
            workspace = output_dir / "workspaces" / f"dry-run-{task.id}-{arm}"
            if workspace.exists():
                _discard_tree(workspace)
            workspace.parent.mkdir(parents=True, exist_ok=True)
            commit = _prepare_workspace(
                task.workspace,
                workspace,
                {MEMORY_FILE: seed_source} if seed_source is not None else None,
                _resolve_memory_snapshot if arm == "relevant" else None,
            )
            clean = not _run_git(workspace, "status", "--porcelain")
            digest = _file_digest(workspace / MEMORY_FILE)
            hint_state, hint_line = _seeded_hint(workspace, spec.anchor_path)
            print(
                f"  arm {arm}: seed={seed_source.name if seed_source else 'none'} "
                f"commit={commit[:12]} worktree_clean={clean} "
                f"memory_sha256={digest or 'absent'} "
                f"hint_state={hint_state or 'none'}"
            )
            if hint_line:
                print(f"    hint: {hint_line}")
            if args.keep_workspaces:
                print(f"    workspace: {workspace}")
                continue
            warning = _discard_tree(workspace)
            if warning is not None:
                print(
                    f"    warning: workspace cleanup failed: {warning}",
                    file=sys.stderr,
                )
    print("\ndry run complete: no provider call was made")


def _seed_source(spec: MemoryScenarioSpec, arm: Arm) -> Path | None:
    if arm == "relevant":
        return spec.relevant
    if arm == "irrelevant":
        return spec.irrelevant
    return None


def _resolve_memory_snapshot(workspace: Path) -> None:
    """把相关记录里的 `{anchor_state}` 解析为锚点文件的真实内容快照。

    快照必须在工作区存在之后才能算，所以占位符由运行器解析；解析只改
    MEMORY.md 的内容，不追加提交，工作区保持干净。
    """
    memory_path = workspace / MEMORY_FILE
    content = memory_path.read_text(encoding="utf-8")
    if _SNAPSHOT_PLACEHOLDER not in content:
        raise ValueError(
            f"relevant seed must contain {_SNAPSHOT_PLACEHOLDER}: {memory_path}"
        )
    anchors = tuple(
        anchor.value
        for record in parse_memory_blocks(content, layer="project")
        for anchor in parse_experience(record).anchors  # type: ignore[union-attr]
        if anchor.kind == "file"
    )
    snapshot = anchor_snapshot(anchors, workspace)
    if snapshot is None:
        raise ValueError(f"anchor snapshot could not be computed: {memory_path}")
    memory_path.write_text(
        content.replace(_SNAPSHOT_PLACEHOLDER, snapshot),
        encoding="utf-8",
    )


def _seeded_hint(workspace: Path, anchor_path: str) -> tuple[str | None, str | None]:
    """用真实提示 API 读取种子记录在锚点上的新鲜度与渲染结果。"""
    hints = select_hints(
        MemoryManager(workspace),
        recent_files=(anchor_path,),
        recent_text="",
    )
    if not hints:
        return None, None
    return hints[0].freshness, render_hint_line(hints[0].experience, hints[0].freshness)


def _ordered_arms(repeat: int) -> tuple[Arm, ...]:
    """按重复次数轮换臂顺序，降低时间顺序偏差。"""
    shift = (repeat - 1) % len(ARMS)
    return (*ARMS[shift:], *ARMS[:shift])


def _branch_tool_calls(store: SessionStore) -> list[dict[str, object]]:
    """按当前分支顺序提取本 session 的工具调用及其入参。"""
    calls: list[dict[str, object]] = []
    for entry in store.build_branch():
        content = entry.content
        if not isinstance(content, dict) or content.get("type") != "tool_use":
            continue
        data = content.get("data")
        if not isinstance(data, dict):
            continue
        raw_input = data.get("input")
        calls.append(
            {
                "id": str(data.get("id", "")),
                "name": str(data.get("name", "")),
                "input": (
                    {str(key): value for key, value in raw_input.items()}
                    if isinstance(raw_input, dict)
                    else {}
                ),
            }
        )
    return calls


def _steps_to_anchor(calls: list[dict[str, object]], anchor_path: str) -> int | None:
    """返回第一个提到锚点路径的工具调用序号；从未提到时返回 None。"""
    for index, call in enumerate(calls, 1):
        if _mentions(call, anchor_path):
            return index
    return None


def _stale_follow(calls: list[dict[str, object]], stale_anchor_path: str) -> bool:
    """首次工具调用就走向无关诱饵锚点，说明被无关经验带偏。"""
    return bool(calls) and _mentions(calls[0], stale_anchor_path)


def _first_write_path(calls: list[dict[str, object]]) -> str | None:
    """返回第一次写入或编辑的目标路径。"""
    for call in calls:
        if str(call.get("name", "")) not in _WRITE_TOOLS:
            continue
        return _tool_path(call)
    return None


def _matches_repo_path(raw_path: str | None, repo_path: str) -> bool:
    """陈旧经验指向错误修复位置时记为被带偏。"""
    if raw_path is None:
        return False
    normalized = raw_path.strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized == repo_path or normalized.endswith(f"/{repo_path}")


def _mentions(call: Mapping[str, object], needle: str) -> bool:
    payload = json.dumps(call.get("input"), ensure_ascii=False, default=str)
    return needle in payload


def _distinct_files_read(calls: list[dict[str, object]]) -> int:
    paths = {path for call in calls if (path := _read_path(call))}
    return len(paths)


def _read_path(call: Mapping[str, object]) -> str:
    if str(call.get("name", "")) not in _READ_TOOLS:
        return ""
    return _tool_path(call) or ""


def _tool_path(call: Mapping[str, object]) -> str | None:
    raw_input = call.get("input")
    if not isinstance(raw_input, dict):
        return None
    value = (
        raw_input.get("path")
        or raw_input.get("file_path")
        or raw_input.get("input")
        or ""
    )
    text = str(value).strip()
    return text or None


def _request_has_memory_block(request_messages: list[Message]) -> bool:
    """请求上下文里出现 memory 提示块标记，即本次请求注入了经验指针。"""
    payload = json.dumps(request_messages, ensure_ascii=False, default=str)
    return _MEMORY_BLOCK_MARKER in payload


def _file_digest(path: Path) -> str | None:
    try:
        content = path.read_bytes()
    except OSError:
        return None
    return hashlib.sha256(content).hexdigest()


def _discard_tree(path: Path) -> str | None:
    """尽力删除临时目录；受限环境拒绝删除时返回警告而不让运行失败。"""
    if not path.exists():
        return None
    try:
        shutil.rmtree(path)
    except OSError as exc:
        return f"{path}: {exc}"
    return None


def _default_output_dir() -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return Path("benchmark-results") / "memory" / stamp


if __name__ == "__main__":
    main()
