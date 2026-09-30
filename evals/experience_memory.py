"""以真实历史缺陷和独立验收比较 Experience 的最终 coding 行为。"""

from __future__ import annotations

import argparse
import asyncio
import difflib
import io
import json
import os
import subprocess
import sys
import tarfile
import time
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

from cade.agent.types import ApprovalRequest, ToolInput, ToolSpec
from cade.coding_agent.app import CadeApp, build_app
from cade.harness.config import CadeRuntimeConfig, discover_runtime_config
from cade.harness.memory import MemoryManager, build_memory_tools
from cade.harness.security.approval import HITLResult
from cade.harness.skills import SkillRegistry

REPO = Path(__file__).resolve().parents[1]
BASE = "972a0936^"
GOLD = "972a09363bbec9d83067179a8cc06e9df16efdcb"
KEY_FILE = "src/cade/coding_agent/tools/_search_utils.py"
TASK = """Fix file discovery ignore precedence. On machines without fd, a file
ignored by .gitignore but explicitly included by .fdignore is missing from find
and glob. Also, a nested .fdignore must not resurrect a file under an excluded
parent directory. Match fd semantics across ripgrep/Python fallbacks and explicit
file paths. Preserve ordinary grep's existing ignore behavior, hidden-file safety,
and truncation. Add your own focused regression and run it with the provided
Python interpreter. Work only in this workspace; no network, delegation, fetching
history, or changing Memory during this experiment. An independent hidden
acceptance test will grade the resulting code, not your answer or your own tests.
For direct Python imports, set PYTHONPATH to this workspace's src directory so
validation loads the candidate code rather than the installed harness.
For this controlled experiment, first call recall with query="fd .fdignore
precedence", limit=1, then investigate current code and validate any hint before
using it. Memory may be empty; continue normally in that case.
"""
VERIFIED = f"""## fd fallback ignore precedence
Type: experience
Problem: Discovery fallback loses .fdignore whitelist entries or revives an
  excluded parent directory's children.
Root cause: Filtering an already gitignored candidate list cannot restore files
  removed by rg. Independent gitignore/fdignore boolean vetoes lose whitelist
  precedence; matching only the leaf ignores ancestor pruning.
Fix pattern: Enumerate candidates before VCS filtering for discovery, then
  resolve the final ordered gitignore/fdignore decision for each path component.
  An excluded ancestor stops traversal; a child whitelist cannot reopen it.
Applies when: Discovery emulates fd using rg/Python. Keep ordinary text grep's
  existing VCS behavior separate. Check current calls before changing flags.
Anchors: fd; .fdignore; {KEY_FILE}; _is_discovery_ignored
Evidence: commit:{GOLD}; test:independent evaluator old=fail, gold=pass
"""
STALE = VERIFIED.replace(
    "Enumerate candidates before VCS filtering for discovery, then\n  resolve the final ordered gitignore/fdignore decision for each path component.\n  An excluded ancestor stops traversal; a child whitelist cannot reopen it.",
    "Keep rg's VCS filtering and simply discard every .fdignore match.\n  Apply nested whitelist rules to leaves even when a parent was excluded.",
).replace("old=fail, gold=pass", "synthetic stale control; not verified")
IRRELEVANT = (
    VERIFIED.replace(
        "Anchors: fd; .fdignore;", "Anchors: sqlite_busy; unrelated_db.py;"
    )
    .replace(KEY_FILE, "src/database.py")
    .replace("_is_discovery_ignored", "sqlite_connect")
)
TRANSCRIPT = (
    VERIFIED.partition("Problem:")[0]
    + "Problem: Investigation transcript follows.\n"
    + "\n".join(
        f"step {index}: read another file; bash returned routine build output; "
        "investigate provider timeouts and retry limits."
        for index in range(160)
    )
    + "\nThe useful discovery insight was:\n"
    + VERIFIED.partition("Problem:")[2]
)
ATOMIC = "## Discovery fact\nType: experience\nHandle fallback correctly.\n"
CONDITIONS = {
    "A": "",
    "B": VERIFIED,
    "C-irrelevant": IRRELEVANT,
    "C-stale": STALE,
    "D": TRANSCRIPT,
    "E": ATOMIC,
}

# 此验收代码仅由 Host 在 solver 完成后执行，不放入其 workspace。
ACCEPTANCE = r"""
import json, shutil, tempfile
from pathlib import Path
from cade.coding_agent.tools import _search_utils as u, glob_search as g
outcomes = {}
rg = shutil.which("rg")
assert rg, "real ripgrep is required"
for scenario in ("whitelist", "nested", "normal"):
 with tempfile.TemporaryDirectory() as raw:
  root = Path(raw)
  base = root / "excluded" if scenario == "nested" else root
  base.mkdir(exist_ok=True)
  target = base / "keep.py"
  target.write_text("needle\n")
  (root / ".hidden.py").write_text("needle\n")
  if scenario == "whitelist":
   (root / ".gitignore").write_text("keep.py\n")
   (root / ".fdignore").write_text("!keep.py\n")
  elif scenario == "nested":
   (root / ".fdignore").write_text("excluded/\n")
   (base / ".fdignore").write_text("!keep.py\n")
  expected = "No files found." if scenario == "nested" else "keep.py"
  for backend in ("python", "rg"):
   u.get_fd_path = lambda: None
   u.get_rg_path = (lambda: None) if backend == "python" else (lambda: rg)
   tools = {t.name:t for t in g.build_glob_tools(root)}
   for tool in ("find", "glob"):
    result = tools[tool].handler({"pattern":"**/*.py"}, None)
    outcomes[f"{scenario}-{backend}-{tool}"] = str(result) == expected
   explicit = u.enumerate_search_files(root, target, use_external=False)
   outcomes[f"{scenario}-{backend}-explicit"] = explicit == ([] if scenario == "nested" else [target])
  if scenario == "whitelist":
   ordinary = u.enumerate_search_files(root, root, respect_fdignore=False)
   outcomes["ordinary-grep-ignore"] = target not in ordinary
  if scenario == "normal":
   (root / "second.py").write_text("needle\n")
   for backend in ("python", "rg"):
    u.get_rg_path = (lambda: None) if backend == "python" else (lambda: rg)
    tools = {t.name:t for t in g.build_glob_tools(root)}
    for tool in ("find", "glob"):
     result = tools[tool].handler({"pattern":"**/*.py", "max_results":1}, None)
     outcomes[f"truncation-{backend}-{tool}"] = "... truncated" in str(result)
print(json.dumps(outcomes))
raise SystemExit(0 if all(outcomes.values()) else 1)
"""


def snapshot(root: Path, ref: str) -> None:
    """使用无 git 历史的独立源码快照，避免目标答案泄漏。"""
    root.mkdir(parents=True)
    archive = subprocess.check_output(["git", "archive", ref], cwd=REPO)
    with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
        bundle.extractall(root, filter="data")
    # 空仓库阻止 git 从源码快照向父目录发现真实历史和 gold patch。
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    actual_root = subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], cwd=root, text=True
    ).strip()
    if Path(actual_root).resolve() != root.resolve():
        raise RuntimeError("Snapshot can discover parent repository history")


def grade(root: Path) -> dict[str, object]:
    """独立进程加载 solver 修改过的代码，不复用 Host 已导入的模块。"""
    result = subprocess.run(
        [sys.executable, "-c", ACCEPTANCE],
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(root / "src")},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return {
        "passed": result.returncode == 0,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def approve(request: ApprovalRequest) -> HITLResult:
    """实验只批准 workspace 内工具；OS sandbox 继续隔离命令执行。"""
    root = Path(request.working_directory).resolve()
    if request.tool.path_extractor is not None:
        for raw in request.tool.path_extractor(request.action_input):
            path = Path(raw).expanduser()
            if not path.is_absolute():
                path = root / path
            if not path.resolve().is_relative_to(root):
                return HITLResult("deny", "once", rationale="Outside eval workspace")
    return HITLResult("allow", "once", rationale="Explicit isolated coding eval")


def controlled_tools(manager: MemoryManager, payload: str) -> tuple[ToolSpec, ...]:
    """受控 form 组只替换 recall 内容，provider 和请求准入仍走真实链路。"""
    original = build_memory_tools(manager)

    def read(data: ToolInput, on_update: object = None) -> str:
        return payload or "No memory matching 'fd .fdignore precedence'."

    return tuple(replace(tool, handler=read) for tool in original)


async def solve(app: CadeApp, run_dir: Path, condition: str) -> dict[str, Any]:
    events: list[dict[str, object]] = []
    started = time.monotonic()
    final = None
    timed_out = False
    try:
        async with asyncio.timeout(240):
            async for event in app.aask_stream(TASK + f"\nPython: {sys.executable}\n"):
                if event.type in {"tool_use", "tool_result"}:
                    events.append({"type": event.type, "data": event.data.__dict__})
                if event.type == "final":
                    final = event.data
    except TimeoutError:
        timed_out = True
    report: dict[str, Any] = {
        "condition": condition,
        "elapsed_seconds": time.monotonic() - started,
        "metrics": final.metrics if final else None,
        "termination": (
            final.termination_reason.value
            if final
            else "timeout"
            if timed_out
            else "missing-final"
        ),
        "answer": final.answer if final else "",
        "tool_calls": (
            [call.__dict__ for call in final.tool_calls]
            if final
            else [event["data"] for event in events if event["type"] == "tool_use"]
        ),
        "events": events,
        "acceptance": grade(run_dir),
        "provider_requests": sum(
            isinstance(entry.content, dict)
            and entry.content.get("type") == "provider_request"
            for entry in app.session_store.build_branch()
        ),
    }
    calls = report["tool_calls"]
    report["first_key_file_call"] = next(
        (
            i
            for i, call in enumerate(calls, 1)
            if call["name"] in {"read", "bash"}
            and KEY_FILE in json.dumps(call["input"])
        ),
        None,
    )
    report["inspection_and_shell_calls"] = sum(
        call["name"] in {"read", "bash", "grep", "glob", "find", "ls"} for call in calls
    )
    return report


async def main(args: argparse.Namespace) -> None:
    config = discover_runtime_config(REPO)
    cfg = CadeRuntimeConfig.model_validate(
        {
            "provider": {
                "model_profiles": {"main": config.provider.model_profiles["main"]}
            },
            "agent": {"max_steps": 18, "max_llm_calls": 18},
            "security": {
                "approval_policy": "on-request",
                "approval_router": "user",
                "non_workspace_access": False,
                "tools": {
                    name: "deny"
                    for name in ("webfetch", "websearch", "delegate", "load_skill")
                },
            },
            "execution_modes": {"default_mode": "build"},
        }
    )
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    base = output / "preflight-old"
    gold = output / "preflight-gold"
    snapshot(base, BASE)
    snapshot(gold, GOLD)
    preflight = {"base": grade(base), "gold": grade(gold)}
    (output / "preflight.json").write_text(json.dumps(preflight, indent=2))
    if preflight["base"]["passed"] or not preflight["gold"]["passed"]:
        raise RuntimeError("Independent acceptance did not distinguish old and gold")
    if args.preflight_only:
        print(json.dumps(preflight), flush=True)
        return
    base_checks = json.loads(str(preflight["base"]["stdout"]))
    summary: list[dict[str, object]] = []
    for repeat in range(args.repeats):
        for name in args.conditions:
            root = output / f"{args.mode}-{repeat}-{name}"
            snapshot(root, BASE)
            before = {
                str(p.relative_to(root)): p.read_text()
                for p in (root / "src").rglob("*.py")
            }
            payload = CONDITIONS[name]
            if payload:
                (root / "MEMORY.md").write_text(payload, encoding="utf-8")
            # 只发现实验技能目录，避免真实用户的技能索引进入 provider 请求。
            with ExitStack() as stack:
                stack.enter_context(
                    patch(
                        "cade.coding_agent.assembly.registry._discover_skills",
                        lambda *args: SkillRegistry(),
                    )
                )
                if args.mode == "form":
                    stack.enter_context(
                        patch(
                            "cade.harness.memory.build_memory_tools",
                            lambda manager, text=payload: controlled_tools(
                                manager, text
                            ),
                        )
                    )
                app = build_app(root, runtime_config=cfg)
            app.agent.user_approval_callback = approve
            if app.agent.current_approval_callback is not approve:
                app.close()
                raise RuntimeError("Eval would issue automatic reviewer inference")
            if app.memory_manager is not None:
                app.memory_manager.user_memory_file = (
                    root / ".evaluation-user-memory.md"
                )
            try:
                report = await solve(app, root, name)
                stdout = report["acceptance"]["stdout"]
                checks = json.loads(stdout) if stdout else None
                report["new_failures_vs_original"] = (
                    [
                        key
                        for key, value in base_checks.items()
                        if value and not checks[key]
                    ]
                    if checks is not None
                    else ["acceptance-crashed"]
                )
                report["model"] = app.get_model_info()["model"]
                report["mode"] = args.mode
                report["repeat"] = repeat
                report["base"] = BASE
                (root / "report.json").write_text(
                    json.dumps(report, default=str, indent=2)
                )
                after = {
                    str(p.relative_to(root)): p.read_text()
                    for p in (root / "src").rglob("*.py")
                }
                changes = "".join(
                    "".join(
                        difflib.unified_diff(
                            before.get(path, "").splitlines(True),
                            after.get(path, "").splitlines(True),
                            fromfile=path,
                            tofile=path,
                        )
                    )
                    for path in sorted(before.keys() | after.keys())
                )
                (root / "changes.diff").write_text(changes)
                summary.append(
                    {
                        key: value
                        for key, value in report.items()
                        if key not in {"events", "answer", "tool_calls"}
                    }
                )
                (output / "summary.json").write_text(json.dumps(summary, indent=2))
                print(json.dumps(summary[-1]), flush=True)
                if report["termination"] == "provider_error":
                    raise RuntimeError(
                        "Real provider unavailable; stopping without synthetic A/B"
                    )
            finally:
                app.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("sparse", "form"), default="sparse")
    parser.add_argument(
        "--conditions", nargs="+", choices=tuple(CONDITIONS), default=list(CONDITIONS)
    )
    parser.add_argument("--repeats", type=int, choices=(1, 2, 3), default=2)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--preflight-only", action="store_true")
    asyncio.run(main(parser.parse_args()))
