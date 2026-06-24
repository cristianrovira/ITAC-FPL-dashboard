import pandas as pd
import pytest

from fpl_dashboard.extraction import detect_interval_hours


@pytest.mark.parametrize(
    ("frequency", "expected"),
    [("15min", 0.25), ("30min", 0.5), ("1h", 1.0)],
)
def test_detect_supported_intervals(frequency, expected):
    timestamps = pd.Series(pd.date_range("2025-01-01", periods=20, freq=frequency))
    detected, warning = detect_interval_hours(timestamps)
    assert detected == expected
    assert warning is None


def test_interval_detection_fails_for_unsupported_spacing():
    timestamps = pd.Series(pd.date_range("2025-01-01", periods=10, freq="20min"))
    detected, warning = detect_interval_hours(timestamps)
    assert detected is None
    assert "not a supported interval" in warning
