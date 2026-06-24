import pandas as pd

from fpl_dashboard.extraction import ExtractedFile, detect_month_year
from fpl_dashboard.validation import missing_months_by_account, validate_files


def test_detect_month_and_year_from_timestamps():
    timestamps = pd.Series(pd.date_range("2025-07-01", periods=96, freq="15min"))
    month, year, warning = detect_month_year(timestamps)
    assert (month, year) == (7, 2025)
    assert warning is None


def test_normal_billing_period_can_cross_calendar_month_without_warning():
    timestamps = pd.Series(pd.date_range("2025-01-15", "2025-02-14 23:45", freq="15min"))
    month, year, warning = detect_month_year(timestamps)
    assert (month, year) == (1, 2025)
    assert warning is None


def test_complete_rolling_year_across_calendar_years_has_no_missing_months():
    periods = pd.period_range("2024-06", "2025-05", freq="M")
    files = [
        ExtractedFile(account="A", filename=f"{period}.xlsx", month=period.month, year=period.year)
        for period in periods
    ]
    assert missing_months_by_account(files)["A"] == []


def test_rolling_window_detects_only_missing_reporting_period():
    periods = [period for period in pd.period_range("2024-06", "2025-05", freq="M") if period != pd.Period("2025-01", freq="M")]
    files = [
        ExtractedFile(account="A", filename=f"{period}.xlsx", month=period.month, year=period.year)
        for period in periods
    ]
    assert missing_months_by_account(files)["A"] == [pd.Period("2025-01", freq="M")]


def test_duplicate_assigned_reporting_month_is_blocking():
    frame = pd.DataFrame({"Demand kW": [1], "__timestamp__": [pd.Timestamp("2025-01-01")]})
    files = [
        ExtractedFile(account="A", filename="one.xlsx", dataframe=frame, month=1, year=2025, demand_columns=["Demand kW"], detected_interval_hours=1),
        ExtractedFile(account="A", filename="two.xlsx", dataframe=frame, month=1, year=2025, demand_columns=["Demand kW"], detected_interval_hours=1),
    ]
    _, errors, _ = validate_files(files)
    assert any("Duplicate reporting month" in error for error in errors)
