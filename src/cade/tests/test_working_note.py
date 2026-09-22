"""工作笔记换窗快照测试。"""

from pathlib import Path

from cade.agent.context import NOTES_MAX_FILE_BYTES
from cade.coding_agent.working_note import (
    capture_working_note,
    render_working_note_restoration,
)


def test_capture_working_note_uses_nonempty_bounded_file(tmp_path: Path) -> None:
    note = tmp_path / "NOTE.md"
    note.write_text("  Next: run tests.  \n", encoding="utf-8")

    assert capture_working_note(tmp_path) == {
        "kind": "working_note",
        "content": "Next: run tests.",
    }

    note.write_text("x" * (NOTES_MAX_FILE_BYTES + 1), encoding="utf-8")
    assert capture_working_note(tmp_path) is None


def test_restoration_skips_unchanged_current_note(tmp_path: Path) -> None:
    (tmp_path / "NOTE.md").write_text("Next: run tests.\n", encoding="utf-8")
    snapshot = {"kind": "working_note", "content": "Next: run tests."}

    assert render_working_note_restoration(tmp_path, snapshot) is None


def test_restoration_injects_snapshot_without_overwriting_changed_note(
    tmp_path: Path,
) -> None:
    note = tmp_path / "NOTE.md"
    note.write_text("Current session state.\n", encoding="utf-8")
    snapshot = {"kind": "working_note", "content": "Old session next action."}

    rendered = render_working_note_restoration(tmp_path, snapshot)

    assert rendered is not None
    assert 'current_note_status="changed"' in rendered
    assert "Old session next action." in rendered
    assert note.read_text(encoding="utf-8") == "Current session state.\n"


def test_restoration_reports_missing_note(tmp_path: Path) -> None:
    rendered = render_working_note_restoration(
        tmp_path,
        {"kind": "working_note", "content": "Resume from this state."},
    )

    assert rendered is not None
    assert 'current_note_status="missing"' in rendered
    assert "Resume from this state." in rendered
