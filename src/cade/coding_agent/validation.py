"""从当前会话分支交接验证事实，不缓存或推断测试结果。"""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
from collections.abc import Mapping
from pathlib import Path

from cade.agent.context import (
    ContextBlock,
    ContextBlockSource,
    ContextBlockTarget,
    ContextCollectionInput,
    ContextPriority,
)
from cade.agent.messages import ToolResultMessage
from cade.agent.types import TerminalRenderIntent, TextContent
from cade.harness.session.tree_store import TreeSessionRepo


def workspace_fingerprint(project_root: Path) -> str | None:
    """指纹覆盖 Git 跟踪及非忽略文件，排除非跟踪的运行时 .cade 目录。"""
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=project_root,
            capture_output=True,
            check=False,
        )
        if result.returncode:
            return None
        tracked = subprocess.run(
            ["git", "ls-files", "-z", "--cached"],
            cwd=project_root,
            capture_output=True,
            check=False,
        )
        if tracked.returncode:
            return None
        tracked_names = set(tracked.stdout.split(b"\0")) - {b""}
        all_names = set(result.stdout.split(b"\0")) - {b""}
        names = tracked_names | {
            name for name in all_names if not name.startswith(b".cade/")
        }
        digest = hashlib.sha256()
        for name in sorted(names):
            digest.update(name + b"\0")
            path = project_root / os.fsdecode(name)
            try:
                mode = path.lstat().st_mode
            except FileNotFoundError:
                digest.update(b"missing\0")
                continue
            digest.update(str(stat.S_IMODE(mode)).encode() + b"\0")
            if path.is_symlink():
                digest.update(os.fsencode(os.readlink(path)))
            elif path.is_file():
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
            else:
                return None
            digest.update(b"\0")
        return digest.hexdigest()
    except OSError:
        return None


class ValidationCollector:
    """用少量原始验证事实覆盖过时笔记，来源始终是当前日志分支。"""

    def __init__(self, project_root: Path, store: TreeSessionRepo) -> None:
        self._root = project_root.resolve()
        self._store = store

    def collect(self, input: ContextCollectionInput) -> list[ContextBlock]:
        entries = self._store.build_branch()
        calls = {}
        for entry in entries:
            content = entry.content
            if not isinstance(content, dict) or content.get("type") != "tool_use":
                continue
            data = content.get("data")
            if isinstance(data, dict):
                calls[str(data.get("id"))] = data
        candidates: list[tuple[str, Mapping[str, object]]] = []
        # 当前回合先于异步日志写入，必须把刚完成的结果立即交付给下一次请求。
        for message in reversed(input.messages):
            if not isinstance(message, ToolResultMessage):
                continue
            if not isinstance(message.render_intent, TerminalRenderIntent):
                continue
            output = message.content
            text = (
                output
                if isinstance(output, str)
                else "\n".join(
                    block.text for block in output if isinstance(block, TextContent)
                )
            )
            metadata = message.metadata or {}
            candidates.append(
                (
                    f"tool_call:{message.tool_call_id}",
                    {
                        "tool_use_id": message.tool_call_id,
                        "metadata": metadata,
                        "render_intent": message.render_intent.model_dump(),
                        "content": text,
                        "exit_code": metadata.get("exit_code"),
                        "status": "error" if message.is_error else "ok",
                    },
                )
            )
        for entry in reversed(entries):
            content = entry.content
            if not isinstance(content, dict) or content.get("type") != "tool_result":
                continue
            data = content.get("data")
            if isinstance(data, dict):
                candidates.append((entry.id, data))
        selected = []
        seen: set[tuple[str, str]] = set()
        for reference, data in candidates:
            call = calls.get(str(data.get("tool_use_id")), {})
            args = call.get("input", {})
            metadata = data.get("metadata", {})
            metadata = metadata if isinstance(metadata, dict) else {}
            purpose = metadata.get("purpose")
            if purpose is None and isinstance(args, dict):
                purpose = args.get("purpose")
            intent = data.get("render_intent")
            if purpose != "validation" or not isinstance(intent, dict):
                continue
            if intent.get("kind") != "terminal":
                continue
            command, cwd = str(intent.get("command", "")), str(intent.get("cwd", ""))
            key = (command, cwd)
            if key in seen:
                continue
            seen.add(key)
            selected.append((reference, data, metadata, command, cwd))
            if len(selected) == 4:
                break
        if not selected:
            return []
        current = workspace_fingerprint(self._root)
        lines = [
            (
                "Recorded validation executions from this session branch. These are raw "
                "process outcomes, not a claim that the task is complete. file_state compares "
                "Git tracked/non-ignored files (untracked .cade excluded); ignored inputs, external "
                "dependencies and environment changes are not covered. Reuse unchanged "
                "evidence; rerun checks for changed code or a specific unresolved uncertainty. "
                "Use history for original full commands; previews are not runnable scripts. "
                "If NOTE.md disagrees with these facts, update its frontier before more "
                "investigation. If the task's required checks are already complete for "
                "unchanged files, finish instead of running them again."
            ),
        ]
        for entry_id, data, metadata, command, cwd in selected:
            state = metadata.get("validation_state")
            freshness = "unknown"
            if isinstance(state, dict):
                before, after = state.get("before"), state.get("after")
                if (
                    isinstance(before, str)
                    and isinstance(after, str)
                    and current is not None
                ):
                    freshness = "unchanged" if before == after == current else "changed"
            if not cwd or not Path(cwd).resolve().is_relative_to(self._root):
                freshness = "unknown"
            code = data.get("exit_code")
            if isinstance(code, bool) or not isinstance(code, int):
                code = "unknown"
            output = str(data.get("content", ""))
            lines.append(
                f"entry_id={entry_id} exit_code={code} status={data.get('status', 'unknown')} file_state={freshness}\n"
                f"cwd={cwd}\ncommand_preview={command[:256]}\noutput_tail={output[-512:]}"
            )
        full = "\n\n".join(lines)
        body = full.encode("utf-8")[:4096].decode("utf-8", errors="ignore")
        return [
            ContextBlock(
                source=ContextBlockSource.RECENT_VALIDATION,
                target=ContextBlockTarget.USER_CONTEXT,
                priority=ContextPriority.HIGH,
                content=body,
                provenance="session:validation",
                scope="runtime",
                truncated=body != full,
                truncation_reason="byte_budget" if body != full else None,
            )
        ]
