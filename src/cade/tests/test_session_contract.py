"""保护会话不可变账本、分支隔离、崩溃恢复及重放契约。

控制性回归使用真实 SessionStore、SessionInbox、SessionHistory 与 replay_session。
用例在 pytest 提供的独立 tmp_path 下运行，断言文件级追加写与树分支不变量。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cade.agent.messages import (
    AgentMessage,
    ContextMessage,
    UserMessage,
)
from cade.agent.types import TextContent, ToolCallContent
from cade.harness.agent_runtime.events import (
    AssistantStructuredEvent,
    AssistantTextBlock,
    AssistantToolUseBlock,
    ContextStructuredEvent,
    ToolResultBlock,
    ToolResultStructuredEvent,
)
from cade.harness.session import (
    InboxLane,
    SessionHistory,
    SessionInbox,
    SessionStore,
)
from cade.harness.session.recorder import SessionRecorder
from cade.harness.session.replay import replay_session
from cade.harness.session.store import read_session_entries
from cade.harness.session.surface import (
    InvalidSessionSurfaceError,
    encode_surface_messages,
    project_session_surface,
    validate_tool_pairing,
)


class DummyReplayAgent:
    """用于验证 replay_session 状态恢复契约的最小测试 Agent。"""

    def __init__(self) -> None:
        self.session_id: str = ""
        self.history_session_id: str = ""
        self.history: list[AgentMessage] = []
        self.run_state: object = None
        self.goal_state: object = None
        self.resumed_notice: str = ""

    def set_history_session_id(self, session_id: str) -> None:
        self.history_session_id = session_id

    def load_history(self, messages: list[AgentMessage]) -> None:
        self.history = list(messages)

    def restore_run_state_metadata(self, payload: object) -> None:
        self.run_state = payload

    def restore_goal_state(self, payload: object) -> None:
        self.goal_state = payload

    def set_resumed_notice(self, notice: str) -> None:
        self.resumed_notice = notice


def test_append_only_event_tree_and_branch_lineage(tmp_path: Path) -> None:
    """验证单分支事件树严格保持 append-only 并在回溯时形成正确线性链路。"""
    sessions_dir = tmp_path / ".cade" / "sessions"
    store = SessionStore(sessions_dir, project_root=tmp_path)

    id1 = store.append("event", {"action": "step1"})
    id2 = store.append("event", {"action": "step2"})
    id3 = store.append("event", {"action": "step3"})

    entries = store.read_entries()
    assert len(entries) == 3
    assert [e.id for e in entries] == [id1, id2, id3]
    assert entries[0].parent_id is None
    assert entries[1].parent_id == id1
    assert entries[2].parent_id == id2

    branch = store.build_branch()
    assert [e.id for e in branch] == [id1, id2, id3]


def test_session_fork_isolation_and_ancestor_immutability(tmp_path: Path) -> None:
    """验证分叉会话继承祖先事件，且双方后续的追加写互不干扰、祖先不可变。"""
    sessions_dir = tmp_path / ".cade" / "sessions"
    parent_store = SessionStore(sessions_dir, project_root=tmp_path)

    p1 = parent_store.append("event", {"step": 1})
    p2 = parent_store.append("event", {"step": 2})
    original_log = parent_store.current_path.read_bytes()

    fork_store = parent_store.fork_into(title="Forked Branch")
    assert fork_store.session_id != parent_store.session_id
    assert parent_store.current_path.read_bytes() == original_log
    assert fork_store.current_path.read_bytes() == original_log

    # 在 fork 上追加写
    f3 = fork_store.append("event", {"step": 3, "branch": "fork"})
    # 在父会话上继续追加写
    p3 = parent_store.append("event", {"step": 3, "branch": "main"})

    parent_branch = parent_store.build_branch()
    fork_branch = fork_store.build_branch()

    assert [e.id for e in parent_branch] == [p1, p2, p3]
    assert [e.id for e in fork_branch] == [p1, p2, f3]

    # 验证祖先事实完全一致
    assert parent_branch[0].content == fork_branch[0].content
    assert parent_branch[1].content == fork_branch[1].content


def test_read_session_entries_recovers_from_uncommitted_trailing_line(
    tmp_path: Path,
) -> None:
    """验证遇到断电/被杀导致的未换行残缺末行时能安全忽略并恢复完整前缀，但换行已提交的损坏行必报错。"""
    session_file = tmp_path / "session-test.jsonl"
    e1 = {
        "id": "e1",
        "parent_id": None,
        "type": "event",
        "content": {"k": "v1"},
        "created_at": "2026-10-14T10:00:00Z",
    }
    e2 = {
        "id": "e2",
        "parent_id": "e1",
        "type": "event",
        "content": {"k": "v2"},
        "created_at": "2026-10-14T10:00:01Z",
    }

    # 写入正常前缀
    content = json.dumps(e1) + "\n" + json.dumps(e2) + "\n"
    session_file.write_text(content, encoding="utf-8")

    # 追加一条没有结尾换行的残损 JSON
    with session_file.open("a", encoding="utf-8") as f:
        f.write('{"id": "e3", "parent_id": "e2", "content": {"broken":')

    # 读取应当安全忽略该末行，不抛异常
    recovered = read_session_entries(session_file)
    assert len(recovered) == 2
    assert [e.id for e in recovered] == ["e1", "e2"]

    # 若损坏行带了换行符（代表已提交却内容非法），必须抛出 ValueError
    with session_file.open("a", encoding="utf-8") as f:
        f.write("\n")
    with pytest.raises(ValueError, match="invalid session record"):
        read_session_entries(session_file)


def test_replay_session_restores_history_via_surface(tmp_path: Path) -> None:
    """验证真实交互事件流经过 SessionStore 持久化后，可通过 replay_session 完整还原模型历史。"""
    sessions_dir = tmp_path / ".cade" / "sessions"
    store = SessionStore(sessions_dir, project_root=tmp_path)

    inbox = SessionInbox(store)
    inbox.insert(UserMessage(content="Hello Cade"), lane=InboxLane.NEXT_TURN, wake=True)
    claimed = inbox.claim_initial(run_id="run-1")
    assert len(claimed) == 1

    # 记录 assistant 回复
    store.append("assistant", "Hello! I am ready to help.")

    agent = DummyReplayAgent()
    replay_session(agent, store)

    assert agent.session_id == store.session_id
    assert agent.history_session_id == store.session_id
    assert len(agent.history) == 2
    assert isinstance(agent.history[0], UserMessage)
    assert agent.history[0].content == "Hello Cade"
    assert isinstance(agent.history[1].content[0], TextContent)
    assert agent.history[1].content[0].text == "Hello! I am ready to help."
    assert "resumed" in agent.resumed_notice.lower()


def test_version_two_session_can_continue_with_durable_runtime_context(
    tmp_path: Path,
) -> None:
    """覆盖版本 2 会话续写版本 3 的缺口，直接检查文件重放及分支入口。"""
    store = SessionStore(tmp_path / "sessions", project_root=tmp_path)
    user_id = store.append(
        "event",
        {
            "schema_version": 2,
            "type": "inbox/claimed",
            "data": {
                "message": encode_surface_messages(
                    [UserMessage(content="Existing task")]
                ),
                "display_text": "Existing task",
            },
        },
    )
    context = ContextMessage(content="Current runtime context: validation passed.")
    recorder = SessionRecorder(store)
    event = ContextStructuredEvent("runtime_context", 1, context)
    recorder.record_event(event)
    recorder.record_event(event)
    store.append("assistant", "Done")
    agent = DummyReplayAgent()
    replay_session(agent, store)
    assert agent.history[0] == UserMessage(content="Existing task")
    assert agent.history[1] == context
    assert len(agent.history) == 3
    assert store.fork_from_entry(user_id).build_branch() == []
    assert SessionInbox(store).pending_count() == 0
    store.append(
        "event",
        {
            "schema_version": 2,
            "type": "runtime_context",
            "data": context.model_dump(mode="json"),
        },
    )
    with pytest.raises(
        InvalidSessionSurfaceError, match="unsupported runtime context schema"
    ):
        project_session_surface(store.build_branch())


def test_new_run_claim_closes_interrupted_batch_before_new_context(
    tmp_path: Path,
) -> None:
    """覆盖中断批次把后续用户输入移到回复之后的缺口。

    运行本模块；真实文件记录部分工具结果、同 run 的 steering 和新 run。
    独立检查完整角色顺序及工具配对，旧未完成调用只能失效，不能伪造结果。
    """
    store = SessionStore(tmp_path / "sessions", project_root=tmp_path)
    inbox = SessionInbox(store)
    recorder = SessionRecorder(store)
    inbox.insert(UserMessage(content="Original task"), InboxLane.NEXT_TURN, wake=True)
    inbox.claim_initial("original-run")
    recorder.record_event(
        AssistantStructuredEvent(
            "assistant",
            1,
            (
                AssistantToolUseBlock("done", "echo", {}),
                AssistantToolUseBlock("interrupted", "echo", {}),
            ),
        )
    )
    recorder.record_event(
        ToolResultStructuredEvent(
            "tool_result", 1, ToolResultBlock("done", "Complete output")
        )
    )
    inbox.insert(
        UserMessage(content="Earlier steering"), InboxLane.NEXT_STEP, wake=False
    )
    inbox.claim_next_step("original-run")
    inbox.insert(UserMessage(content="Resume the task"), InboxLane.NEXT_TURN, wake=True)
    inbox.claim_initial("new-run")
    snapshot = ContextMessage(content="Current runtime context after interruption")
    recorder.record_event(ContextStructuredEvent("runtime_context", 1, snapshot))
    recorder.record_event(
        AssistantStructuredEvent(
            "assistant", 1, (AssistantTextBlock("Recovered answer"),)
        )
    )
    inbox.insert(UserMessage(content="Next task"), InboxLane.NEXT_TURN, wake=True)
    inbox.claim_initial("third-run")

    surface = project_session_surface(store.build_branch())
    messages = list(surface.messages)
    assert [m.role for m in messages] == [
        "user",
        "assistant",
        "tool_result",
        "user",
        "user",
        "context",
        "assistant",
        "user",
    ]
    assert [m.content for m in messages if isinstance(m, UserMessage)] == [
        "Original task",
        "Earlier steering",
        "Resume the task",
        "Next task",
    ]
    assert messages[5] == snapshot
    assert [
        b.id
        for m in messages
        if m.role == "assistant"
        for b in m.content
        if isinstance(b, ToolCallContent)
    ] == ["done"]
    validate_tool_pairing(messages)


def test_session_history_around_and_anchor_scoping(tmp_path: Path) -> None:
    """验证 SessionHistory 的 around 检索：当前分支支持前后邻域，跨 Session 仅支持祖先链并拒绝 after。"""
    sessions_dir = tmp_path / ".cade" / "sessions"
    store = SessionStore(sessions_dir, project_root=tmp_path)

    ids = [store.append("event", {"step": i}) for i in range(5)]

    history = SessionHistory(sessions_dir, project_root=tmp_path)
    history.set_session_id(store.session_id)

    # 在当前分支上围绕中间节点 ids[2] 查询：前后各 1 个
    window = history.around(ids[2], before=1, after=1)
    assert [e.id for e in window] == [ids[1], ids[2], ids[3]]

    # 显式跨 session 查询指定 anchor ids[2] 时如果传入 after 必须拒绝
    with pytest.raises(
        ValueError, match="after is only supported on the current branch"
    ):
        history.around(ids[2], before=2, after=2, session_id=store.session_id)

    # 显式跨 session 查询在不传 after 时，严格限制在祖先链范围（ids[2] 为末尾）
    cross_window = history.around(ids[2], before=2, session_id=store.session_id)
    assert [e.id for e in cross_window] == [ids[0], ids[1], ids[2]]
