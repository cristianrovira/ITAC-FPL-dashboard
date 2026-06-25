from datetime import time

from fpl_dashboard.schedule_ui import _coerce_time, _days_for_preset, _parse_days, _preset_shifts, _schedule_frame


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
    assert frame.loc[0, "Days"] == "Mon, Tue"
    assert frame["Active"].all()


def test_parse_days_supports_per_shift_weekday_and_weekend_entries():
    assert _parse_days("Mon-Fri") == [0, 1, 2, 3, 4]
    assert _parse_days("Saturday, Sunday") == [5, 6]
    assert _parse_days("Sat-Sun") == [5, 6]
    assert _parse_days("weekends") == [5, 6]
    assert _parse_days("24/7") == [0, 1, 2, 3, 4, 5, 6]


def test_midnight_time_text_is_accepted():
    assert _coerce_time("12:00 AM") == time(0)
    assert _coerce_time("midnight") == time(0)
    assert _coerce_time("24:00") == time(0)
