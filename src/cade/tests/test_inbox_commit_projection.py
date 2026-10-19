"""验证 inbox 缓存只跟随已提交事实，不独立决定输入是否已消费。

覆盖缺口：claim/discard 先改缓存，失败后会丢掉唤醒；reload 读取日志后
再加锁，会覆盖并发插入。运行本模块，注入提交前后失败并独立读取日志
计算待处理输入，再检查真实 inbox 的下一次认领和并发插入结果。
"""

from __future__ import annotations

import threading
from collections import Counter
from pathlib import Path

import pytest

from cade.agent.messages import UserMessage
from cade.harness.session.inbox import InboxLane, SessionInbox
from cade.harness.session.read_model import SessionReadView
from cade.harness.session.store import SessionStore
from cade.harness.session.types import JsonValue


@pytest.mark.parametrize(
    "operation", ["insert", "runtime", "initial", "step", "discard"]
)
@pytest.mark.parametrize("failure_point", ["before", "after"])
def test_inbox_projection_matches_facts_after_partial_commit_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str, failure_point: str
) -> None:
    store = SessionStore(tmp_path / "sessions")
    inbox = SessionInbox(store)
    if operation in {"initial", "step", "discard"}:
        for text, lane in (
            ("First", InboxLane.NEXT_STEP),
            ("Second", InboxLane.NEXT_STEP),
            ("Later", InboxLane.NEXT_TURN),
        ):
            inbox.insert(UserMessage(content=text), lane, wake=True)
    event_type = (
        "inbox/inserted"
        if operation == "insert"
        else "inbox/discarded"
        if operation == "discard"
        else "inbox/claimed"
    )
    fail_at = 2 if operation in {"initial", "step", "discard"} else 1
    original = store.append
    calls = 0

    def fail_append(record_type: str, content: JsonValue) -> str:
        nonlocal calls
        targeted = isinstance(content, dict) and content.get("type") == event_type
        if targeted:
            calls += 1
        if targeted and calls == fail_at and failure_point == "before":
            raise OSError("Injected inbox commit failure")
        result = original(record_type, content)
        if targeted and calls == fail_at and failure_point == "after":
            raise OSError("Injected inbox commit failure")
        return result

    monkeypatch.setattr(store, "append", fail_append)
    with pytest.raises(OSError, match="Injected inbox commit failure"):
        if operation == "insert":
            inbox.insert(UserMessage(content="New"), InboxLane.NEXT_STEP, wake=True)
        elif operation == "runtime":
            inbox.claim_runtime(UserMessage(content="Runtime"), "run-1")
        elif operation == "initial":
            inbox.claim_initial("run-1")
        elif operation == "step":
            inbox.claim_next_step("run-1")
        else:
            inbox.discard_all("discarded")
    monkeypatch.setattr(store, "append", original)
    pending: dict[str, str] = {}
    for entry in store.build_branch():
        fact = entry.content
        if not isinstance(fact, dict) or not isinstance(data := fact.get("data"), dict):
            continue
        if fact.get("type") == "inbox/inserted":
            pending[data["inbox_id"]] = data["message"][0]["payload"]["content"]
        elif fact.get("type") in {"inbox/claimed", "inbox/discarded"}:
            pending.pop(data["inbox_id"], None)
    assert inbox.pending_count() == SessionInbox(store).pending_count() == len(pending)
    assert inbox.has_waking_input() == bool(pending)
    assert [message.content for message in inbox.claim_initial("recovery")] == list(
        pending.values()
    )
    claims = Counter(
        entry.content["data"]["inbox_id"]
        for entry in store.build_branch()
        if isinstance(entry.content, dict)
        and entry.content.get("type") == "inbox/claimed"
    )
    assert all(count == 1 for count in claims.values())


def test_reload_does_not_overwrite_concurrent_insert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SessionStore(tmp_path / "sessions")
    inbox = SessionInbox(store)
    inbox.insert(UserMessage(content="Original"), InboxLane.NEXT_STEP, wake=True)
    snapshot_read, release_snapshot = threading.Event(), threading.Event()
    inserting, inserted = threading.Event(), threading.Event()
    failures: list[Exception] = []
    original = store.read_view

    def read_snapshot() -> SessionReadView:
        records = original()
        if threading.current_thread().name == "inbox-reload-probe":
            snapshot_read.set()
            if not release_snapshot.wait(timeout=5):
                raise TimeoutError("snapshot probe was not released")
        return records

    monkeypatch.setattr(store, "read_view", read_snapshot)

    def reload() -> None:
        try:
            inbox.reload()
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            failures.append(exc)

    def insert() -> None:
        try:
            inserting.set()
            inbox.insert(
                UserMessage(content="Concurrent"), InboxLane.NEXT_STEP, wake=True
            )
            inserted.set()
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            failures.append(exc)

    reader = threading.Thread(target=reload, name="inbox-reload-probe")
    writer = threading.Thread(target=insert, name="inbox-insert-probe")
    reader.start()
    try:
        assert snapshot_read.wait(timeout=3)
        writer.start()
        assert inserting.wait(timeout=3)
        # 旧实现允许 insert 先完成，再用旧日志快照覆盖缓存。
        inserted.wait(timeout=0.5)
    finally:
        release_snapshot.set()
        reader.join(timeout=3)
        if writer.ident is not None:
            writer.join(timeout=3)
    assert not reader.is_alive() and not writer.is_alive()
    assert not failures
    assert inbox.pending_count() == SessionInbox(store).pending_count() == 2
    assert [message.content for message in inbox.claim_next_step("run")] == [
        "Original",
        "Concurrent",
    ]
