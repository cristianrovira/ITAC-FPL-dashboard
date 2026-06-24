from datetime import time

from fpl_dashboard.schedule_ui import _apply_editor_state, _schedule_frame


def test_data_editor_state_preserves_time_edit_before_rerun():
    rows = _schedule_frame("Standard business hours", ["Monday", "Tuesday"])
    updated = _apply_editor_state(
        rows,
        {"edited_rows": {0: {"Start time": "09:00 AM"}}},
    )
    assert updated.loc[0, "Start time"] == "09:00 AM"


def test_data_editor_state_supports_custom_added_and_deleted_shifts():
    rows = _schedule_frame("Two shifts", ["Monday"])
    updated = _apply_editor_state(
        rows,
        {
            "deleted_rows": [0],
            "added_rows": [
                {
                    "Shift name": "Night shift",
                    "Days": "Mon",
                    "Start time": time(23),
                    "End time": time(6, 30),
                    "Active": True,
                }
            ],
        },
    )
    assert list(updated["Shift name"]) == ["Shift 2", "Night shift"]
