from datetime import time

import pandas as pd

from fpl_dashboard.classification import (
    apply_timestamp_alignment,
    classify_idle_load,
    classify_operating,
    idle_load_threshold,
    is_on_peak,
    timestamp_in_continuous_window,
    is_operating,
)


def test_operating_and_non_operating_classification():
    shifts = [{"days": [0, 1, 2, 3, 4], "start": time(8), "end": time(17), "active": True}]
    assert is_operating(pd.Timestamp("2025-06-02 09:00"), shifts)
    assert not is_operating(pd.Timestamp("2025-06-02 18:00"), shifts)
    assert not is_operating(pd.Timestamp("2025-06-07 09:00"), shifts)


def test_separate_weekday_and_weekend_shifts_can_share_times():
    shifts = [
        {"days": [0, 1, 2, 3, 4], "start": time(8), "end": time(0), "active": True},
        {"days": [5, 6], "start": time(8), "end": time(0), "active": True},
    ]
    assert is_operating(pd.Timestamp("2025-06-06 23:45"), shifts)  # Friday shift
    assert is_operating(pd.Timestamp("2025-06-07 23:45"), shifts)  # Saturday shift
    assert is_operating(pd.Timestamp("2025-06-08 23:45"), shifts)  # Sunday shift
    assert not is_operating(pd.Timestamp("2025-06-06 07:59"), shifts)
    assert not is_operating(pd.Timestamp("2025-06-07 00:30"), shifts)


def test_overnight_shift_uses_starting_day():
    shifts = [{"days": [0, 1, 2, 3, 4], "start": time(23), "end": time(6, 30), "active": True}]
    assert is_operating(pd.Timestamp("2025-06-02 23:30"), shifts)  # Monday
    assert is_operating(pd.Timestamp("2025-06-03 02:00"), shifts)  # Monday's shift
    assert not is_operating(pd.Timestamp("2025-06-02 02:00"), shifts)  # Sunday was not operating


def test_around_the_clock_schedule_marks_every_day_operating():
    shifts = [{"days": list(range(7)), "start": time(0), "end": time(0), "active": True}]
    timestamps = pd.Series(pd.to_datetime(["2025-06-07 02:00", "2025-06-09 14:00"]))
    assert classify_operating(timestamps, shifts).tolist() == [True, True]


def test_legacy_on_peak_classification():
    assert is_on_peak(pd.Timestamp("2025-07-02 13:00"))
    assert not is_on_peak(pd.Timestamp("2025-07-02 10:00"))
    assert is_on_peak(pd.Timestamp("2025-01-02 07:00"))
    assert is_on_peak(pd.Timestamp("2025-01-02 19:00"))
    assert not is_on_peak(pd.Timestamp("2025-01-04 19:00"))  # Saturday



def test_exact_on_peak_excludes_boundary_end_times():
    assert is_on_peak(pd.Timestamp("2025-07-02 12:00"))
    assert not is_on_peak(pd.Timestamp("2025-07-02 21:00"))
    assert is_on_peak(pd.Timestamp("2025-01-02 06:00"))
    assert not is_on_peak(pd.Timestamp("2025-01-02 10:00"))
    assert is_on_peak(pd.Timestamp("2025-01-02 18:00"))
    assert not is_on_peak(pd.Timestamp("2025-01-02 22:00"))


def test_timestamp_alignment_can_use_interval_end_or_midpoint():
    timestamps = pd.Series(pd.to_datetime(["2025-06-02 08:15"]))
    intervals = pd.Series([0.25])
    assert apply_timestamp_alignment(timestamps, intervals, "end").iloc[0] == pd.Timestamp("2025-06-02 08:00")
    assert apply_timestamp_alignment(timestamps, intervals, "midpoint").iloc[0] == pd.Timestamp("2025-06-02 08:22:30")


def test_idle_load_classification_uses_demand_cutoff():
    demand = pd.Series([100, 120, 150, 300, 350])
    threshold = idle_load_threshold(demand, 0.40)
    classified = classify_idle_load(demand, 0.40)
    assert threshold == 138
    assert classified.tolist() == [False, False, True, True, True]



def test_sunday_noon_to_friday_7pm_schedule_boundaries():
    shifts = [
        {"days": [6], "start": time(12), "end": time(0), "active": True},
        {"days": [0, 1, 2, 3], "start": time(0), "end": time(0), "active": True},
        {"days": [4], "start": time(0), "end": time(19), "active": True},
    ]
    assert not is_operating(pd.Timestamp("2025-07-06 11:45"), shifts)  # Sunday before noon
    assert is_operating(pd.Timestamp("2025-07-06 12:00"), shifts)  # Sunday noon
    assert is_operating(pd.Timestamp("2025-07-07 03:00"), shifts)  # Monday overnight
    assert is_operating(pd.Timestamp("2025-07-10 23:45"), shifts)  # Thursday late night
    assert is_operating(pd.Timestamp("2025-07-11 18:45"), shifts)  # Friday before 7 PM
    assert not is_operating(pd.Timestamp("2025-07-11 19:00"), shifts)  # Friday after shutdown
    assert not is_operating(pd.Timestamp("2025-07-12 12:00"), shifts)  # Saturday



def test_continuous_operating_window_sunday_noon_to_friday_7pm():
    shift = {
        "type": "continuous_window",
        "start_day": 6,
        "start": time(12),
        "end_day": 4,
        "end": time(19),
        "active": True,
    }
    assert not timestamp_in_continuous_window(pd.Timestamp("2025-07-06 11:45"), shift)
    assert timestamp_in_continuous_window(pd.Timestamp("2025-07-06 12:00"), shift)
    assert timestamp_in_continuous_window(pd.Timestamp("2025-07-07 12:00"), shift)
    assert timestamp_in_continuous_window(pd.Timestamp("2025-07-10 12:00"), shift)
    assert timestamp_in_continuous_window(pd.Timestamp("2025-07-11 18:45"), shift)
    assert not timestamp_in_continuous_window(pd.Timestamp("2025-07-11 19:00"), shift)
    assert not timestamp_in_continuous_window(pd.Timestamp("2025-07-12 12:00"), shift)


def test_continuous_operating_window_wraps_across_week_end():
    shift = {
        "type": "continuous_window",
        "start_day": 4,
        "start": time(22),
        "end_day": 0,
        "end": time(6),
        "active": True,
    }
    assert not is_operating(pd.Timestamp("2025-07-11 21:59"), [shift])
    assert is_operating(pd.Timestamp("2025-07-11 22:00"), [shift])
    assert is_operating(pd.Timestamp("2025-07-12 12:00"), [shift])
    assert is_operating(pd.Timestamp("2025-07-13 23:00"), [shift])
    assert is_operating(pd.Timestamp("2025-07-14 05:59"), [shift])
    assert not is_operating(pd.Timestamp("2025-07-14 06:00"), [shift])


def test_inactive_continuous_window_is_ignored():
    shift = {
        "type": "continuous_window",
        "start_day": 0,
        "start": time(0),
        "end_day": 0,
        "end": time(0),
        "active": False,
    }
    assert not is_operating(pd.Timestamp("2025-07-07 12:00"), [shift])
