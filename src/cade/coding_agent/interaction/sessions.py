from __future__ import annotations

from cade.harness.session import SessionInfoView as SessionMetadataView
from cade.harness.session import SessionStore


def resume_latest(store: SessionStore) -> SessionMetadataView | None:
    sessions = store.list_infos(limit=1)
    if not sessions:
        return None
    store.resume(sessions[0].id)
    return sessions[0]


def current_view(store: SessionStore) -> SessionMetadataView:
    metadata = store.current_metadata()
    if metadata is not None:
        return SessionMetadataView(
            id=metadata.id,
            title=metadata.title,
            summary=metadata.summary,
            updated_at=metadata.updated_at,
            path=store.current_path,
        )
    session_id = store.current_path.stem.removeprefix("session-")
    return SessionMetadataView(
        id=session_id,
        title=f"Session {session_id}",
        summary="No summary available.",
        updated_at="",
        path=store.current_path,
    )


def resumed_message(view: SessionMetadataView) -> str:
    return f"Resumed conversation: {view.title}"
