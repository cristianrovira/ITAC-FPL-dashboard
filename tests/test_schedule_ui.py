from datetime import time

from fpl_dashboard.schedule_ui import _days_for_preset, _preset_shifts, _schedule_frame


def test_standard_business_hours_defaults():
    shifts = _preset_shifts("Standard business hours")
    assert shifts == [("Shift 1", time(8), time(17))]
    assert _days_for_preset("Standard business hours") == [
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
    ]


def test_schedule_frame_keeps_editable_time_values():
    frame = _schedule_frame("Two shifts", ["Monday", "Tuesday"])
    assert frame.loc[0, "Start time"] == "06:30 AM"
    assert frame.loc[1, "End time"] == "11:00 PM"
    assert frame["Active"].all()
