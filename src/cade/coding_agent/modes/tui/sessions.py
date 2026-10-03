from __future__ import annotations

import questionary

from cade.harness.session import SessionInfoView as SessionMetadataView

from .ptk_patch import safe_select


def select_session_interactively(
    sessions: list[SessionMetadataView],
    title: str,
) -> SessionMetadataView | None:
    """显示支持方向键、鼠标和数字键选择的会话列表。"""
    choices = _session_choices(sessions)
    if not choices:
        return None
    return _run_session_picker(title, choices)


def _session_choices(
    sessions: list[SessionMetadataView],
) -> list[tuple[SessionMetadataView, str]]:
    """构建会话选择项。"""
    id_to_index = {session.id: str(index) for index, session in enumerate(sessions, 1)}
    choices: list[tuple[SessionMetadataView, str]] = []
    for item in sessions:
        title = item.title
        if item.parent_id and item.parent_id in id_to_index:
            title += f" (forked from #{id_to_index[item.parent_id]})"
        if item.summary:
            title += f" - {item.summary}"
        choices.append((item, title[:120]))
    return choices


def _run_session_picker(
    title: str,
    choices: list[tuple[SessionMetadataView, str]],
) -> SessionMetadataView | None:
    """显示会话选择器。"""
    questionary_choices = [
        questionary.Choice(title=label, value=session) for session, label in choices
    ]
    return safe_select(title, choices=questionary_choices)
