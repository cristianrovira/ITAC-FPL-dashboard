from datetime import time

import pandas as pd

from fpl_dashboard.classification import is_on_peak, is_operating


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


def test_legacy_on_peak_classification():
    assert is_on_peak(pd.Timestamp("2025-07-02 13:00"))
    assert not is_on_peak(pd.Timestamp("2025-07-02 10:00"))
    assert is_on_peak(pd.Timestamp("2025-01-02 07:00"))
    assert is_on_peak(pd.Timestamp("2025-01-02 19:00"))
    assert not is_on_peak(pd.Timestamp("2025-01-04 19:00"))  # Saturday
