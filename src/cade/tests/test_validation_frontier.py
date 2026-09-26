"""验证事实跨换窗及重启恢复，使用真实 shell、Git 和 session 文件。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from cade.agent.context import ContextCollectionInput
from cade.coding_agent.tools.bash import build_bash_tool
from cade.coding_agent.validation import ValidationCollector, workspace_fingerprint
from cade.harness.agent_runtime.events import ToolResultBlock, ToolResultStructuredEvent
from cade.harness.session import SessionStore
from cade.harness.session.recorder import SessionRecorder


def _workspace(root: Path) -> Path:
    workspace = root / "workspace"
    workspace.mkdir()
    (workspace / ".gitignore").write_text("NOTE.md\n__pycache__/\n")
    (workspace / "input.txt").write_text("original\n")
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    subprocess.run(
        ["git", "-C", str(workspace), "add", "input.txt", ".gitignore"], check=True
    )
    return workspace


def test_validation_survives_rollover_resume_and_invalidates_edits(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    store = SessionStore(tmp_path / "sessions", project_root=workspace)
    recorder = SessionRecorder(store)
    tool = build_bash_tool(workspace)
    command = (
        "python -c 'from pathlib import Path; print(Path(\"input.txt\").read_text())'"
    )
    output = tool.handler({"command": command, "purpose": "validation"}, None)
    metadata = output.metadata
    assert metadata is not None
    assert (
        metadata["validation_state"]["before"] == metadata["validation_state"]["after"]
    )
    store.append(
        "event",
        {
            "type": "tool_use",
            "data": {
                "id": "check",
                "name": "bash",
                "input": {"command": command, "purpose": "validation"},
            },
        },
    )
    recorder.record_event(
        ToolResultStructuredEvent(
            "tool_result",
            1,
            ToolResultBlock(
                tool_use_id="check",
                content=str(output),
                exit_code=0,
                render_intent=output.render_intent,
                metadata=metadata,
            ),
        )
    )
    recorder.record_context_window_reset(
        window_id="fresh", messages_before=2, messages_after=0, replacement=[]
    )
    resumed = SessionStore(tmp_path / "sessions", project_root=workspace)
    resumed.resume(store.session_id)
    collector = ValidationCollector(workspace, resumed)
    inputs = ContextCollectionInput(messages=[], project_root=workspace)
    before = collector.collect(inputs)
    assert "exit_code=0" in before[0].content
    assert "file_state=unchanged" in before[0].content
    (workspace / "NOTE.md").write_text("verification complete")
    assert collector.collect(inputs)[0].content == before[0].content
    (workspace / "input.txt").write_text("changed\n")
    after = collector.collect(inputs)
    assert "file_state=changed" in after[0].content
    (tmp_path / "frontier.json").write_text(
        json.dumps({"before": before[0].content, "after": after[0].content})
    )


def test_workspace_fingerprint_tracks_new_deleted_and_modified_files(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    first = workspace_fingerprint(workspace)
    assert first is not None
    (workspace / "new.txt").write_text("new")
    assert workspace_fingerprint(workspace) != first
    (workspace / "new.txt").unlink()
    assert workspace_fingerprint(workspace) == first
    (workspace / "input.txt").unlink()
    assert workspace_fingerprint(workspace) != first
    assert workspace_fingerprint(tmp_path) is None


def test_latest_result_overrides_failure_and_does_not_claim_unknown_state(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    store = SessionStore(tmp_path / "sessions", project_root=workspace)
    for call_id, code in (("failed", 1), ("passed", 0)):
        store.append(
            "event",
            {
                "type": "tool_use",
                "data": {
                    "id": call_id,
                    "name": "bash",
                    "input": {"command": "check", "purpose": "validation"},
                },
            },
        )
        store.append(
            "event",
            {
                "type": "tool_result",
                "data": {
                    "tool_use_id": call_id,
                    "exit_code": code,
                    "status": "error" if code else "ok",
                    "content": "raw result",
                    "render_intent": {
                        "kind": "terminal",
                        "command": "check",
                        "cwd": str(workspace),
                    },
                },
            },
        )
    blocks = ValidationCollector(workspace, store).collect(
        ContextCollectionInput(messages=[], project_root=workspace)
    )
    assert len(blocks) == 1
    assert "exit_code=0" in blocks[0].content
    assert "file_state=unknown" in blocks[0].content
    assert "failed" not in blocks[0].content


def test_live_result_is_available_before_recorder_catches_up(tmp_path: Path) -> None:
    from cade.agent.messages import ToolResultMessage

    workspace = _workspace(tmp_path)
    store = SessionStore(tmp_path / "sessions", project_root=workspace)
    output = build_bash_tool(workspace).handler(
        {"command": "python -c 'print(\"passed\")'", "purpose": "validation"}, None
    )
    collector = ValidationCollector(workspace, store)
    blocks = collector.collect(
        ContextCollectionInput(
            messages=[
                ToolResultMessage(
                    tool_call_id="live",
                    tool_name="bash",
                    content=str(output),
                    metadata=output.metadata,
                    render_intent=output.render_intent,
                )
            ],
            project_root=workspace,
        )
    )
    assert len(blocks) == 1
    assert "file_state=unchanged" in blocks[0].content
    assert "exit_code=0" in blocks[0].content
    assert "passed" in blocks[0].content
