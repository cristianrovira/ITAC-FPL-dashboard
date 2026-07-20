from __future__ import annotations

import pandas as pd
import pytest

from fpl_dashboard.estimation import circular_month_distance, estimate_missing_months
from fpl_dashboard.processing import DEMAND_COLUMNS, ENERGY_COLUMNS


def _consistent_row(year: int, month: int, value: float, account: str = "A") -> dict[str, object]:
    row: dict[str, object] = {
        "Account": account,
        "Year": year,
        "Month": month,
        "Data Source": "Actual",
        "Estimate Method": "Actual uploaded interval file",
        "Confidence": "Normal",
        "Peak During Non-Operating": False,
        "Coverage %": 100.0,
        "Coverage Status": "Complete",
        "Total Rows": 100,
        "Operating Rows": 80,
        "Not Operating Rows": 20,
    }
    for column in ENERGY_COLUMNS + DEMAND_COLUMNS:
        row[column] = float(value)
    row["Operating kWh"] = value * 0.75
    row["Non-Operating kWh"] = value * 0.25
    row["On-Peak Operating kWh"] = value * 0.25
    row["Off-Peak Operating kWh"] = value * 0.50
    row["On-Peak Non-Operating kWh"] = value * 0.10
    row["Off-Peak Non-Operating kWh"] = value * 0.15
    row["On-Peak kWh"] = value * 0.35
    row["Off-Peak kWh"] = value * 0.65
    row["Weekend kWh"] = value * 0.20
    row["Overnight kWh"] = value * 0.30
    row["Non-Operating %"] = 25.0
    return row


def _summary(period_values: dict[tuple[int, int], float]) -> pd.DataFrame:
    return pd.DataFrame([_consistent_row(year, month, value) for (year, month), value in period_values.items()])


def _value(completed: pd.DataFrame, year: int, month: int) -> float:
    match = (completed["Year"] == year) & (completed["Month"] == month)
    return float(completed.loc[match, "Total kWh"].iloc[0])


def _row(completed: pd.DataFrame, year: int, month: int) -> pd.Series:
    match = (completed["Year"] == year) & (completed["Month"] == month)
    return completed.loc[match].iloc[0]


def _periods(frame: pd.DataFrame) -> pd.PeriodIndex:
    return pd.PeriodIndex.from_fields(year=frame["Year"], month=frame["Month"], freq="M")


def _old_leading_linear_estimate(target: pd.Period, actuals: dict[pd.Period, float]) -> float:
    anchors = sorted(actuals)
    anchor = anchors[0]
    neighbor = anchors[1]
    steps = anchor.ordinal - target.ordinal
    distance = neighbor.ordinal - anchor.ordinal
    monthly_change = (actuals[anchor] - actuals[neighbor]) / distance
    return actuals[anchor] + monthly_change * steps


def test_circular_month_distance_wraps_calendar_boundaries():
    assert circular_month_distance(1, 12) == 1
    assert circular_month_distance(12, 1) == 1
    assert circular_month_distance(8, 7) == 1
    assert circular_month_distance(8, 11) == 3


def test_august_missing_uses_july_as_circular_adjacent_anchor():
    windows = {"A": pd.period_range("2025-08", "2026-07", freq="M")}
    completed, notes = estimate_missing_months(_summary({(2025, 11): 100, (2026, 7): 200}), windows)
    august = _row(completed, 2025, 8)
    august_note = notes.loc[notes["Estimated month"] == "August 2025"].iloc[0]

    assert august["Data Source"] == "Estimated"
    assert august["Total kWh"] > 100
    assert august["Total kWh"] > _value(completed, 2025, 10)
    assert "Circular interpolation" in august["Estimate Method"]
    assert "July 2026" in august_note["Local reference months"]
    assert "November 2025" in august_note["Local reference months"]


def test_missing_january_uses_december_and_february_across_years():
    windows = {"A": pd.period_range("2024-12", "2025-02", freq="M")}
    completed, _ = estimate_missing_months(_summary({(2024, 12): 120, (2025, 2): 20}), windows)
    row = _row(completed, 2025, 1)

    assert row["Total kWh"] == pytest.approx(70)
    assert "December 2024" in row["Estimate Method"]
    assert "February 2025" in row["Estimate Method"]


def test_missing_december_uses_november_and_january_across_years():
    windows = {"A": pd.period_range("2024-11", "2025-01", freq="M")}
    completed, _ = estimate_missing_months(_summary({(2024, 11): 110, (2025, 1): 10}), windows)
    row = _row(completed, 2024, 12)

    assert row["Total kWh"] == pytest.approx(60)
    assert "November 2024" in row["Estimate Method"]
    assert "January 2025" in row["Estimate Method"]


def test_august_through_october_leading_gap_uses_seasonal_support_not_flat_november_copy():
    windows = {"A": pd.period_range("2025-08", "2026-07", freq="M")}
    actuals = {
        (2025, 11): 100,
        (2025, 12): 110,
        (2026, 1): 120,
        (2026, 2): 130,
        (2026, 3): 140,
        (2026, 4): 150,
        (2026, 5): 160,
        (2026, 6): 180,
        (2026, 7): 220,
    }
    completed, notes = estimate_missing_months(_summary(actuals), windows)

    august = _value(completed, 2025, 8)
    september = _value(completed, 2025, 9)
    october = _value(completed, 2025, 10)
    assert august > september > october > 100
    assert all("July 2026" in method for method in completed.loc[completed["Month"].isin([8, 9, 10]), "Estimate Method"])
    assert "July 2026" in notes.loc[notes["Estimated month"] == "August 2025", "Seasonal reference months"].iloc[0]


def test_same_month_from_another_year_receives_highest_priority():
    windows = {"A": pd.period_range("2026-07", "2027-06", freq="M")}
    completed, notes = estimate_missing_months(_summary({(2025, 8): 300, (2026, 7): 200, (2026, 9): 100}), windows)
    row = _row(completed, 2026, 8)
    note = notes.loc[notes["Estimated month"] == "August 2026"].iloc[0]

    assert "Same-month reference" in row["Estimate Method"]
    assert "August 2025" in note["Same-month reference"]
    assert row["Confidence"] == "High Estimate Confidence"
    assert row["Total kWh"] > 200


def test_two_sided_chronological_interpolation_still_works():
    windows = {"A": pd.period_range("2025-02", "2025-04", freq="M")}
    completed, _ = estimate_missing_months(_summary({(2025, 2): 20, (2025, 4): 40}), windows)

    assert _value(completed, 2025, 3) == pytest.approx(30)
    assert "Linear interpolation" in _row(completed, 2025, 3)["Estimate Method"]


def test_long_leading_gap_with_weak_support_is_low_or_very_low_confidence():
    windows = {"A": pd.period_range("2025-01", "2025-12", freq="M")}
    completed, _ = estimate_missing_months(_summary({(2025, 11): 100}), windows)
    estimated = completed[completed["Data Source"] == "Estimated"]

    assert set(estimated["Confidence"]).issubset({"Low", "Very Low"})
    assert "Very Low" in set(estimated["Confidence"])


def test_long_trailing_gap_uses_circular_seasonal_support_when_available():
    windows = {"A": pd.period_range("2025-01", "2025-12", freq="M")}
    completed, notes = estimate_missing_months(_summary({(2025, 1): 100, (2025, 10): 190}), windows)
    december_note = notes.loc[notes["Estimated month"] == "December 2025"].iloc[0]

    assert "January 2025" in december_note["Local reference months"]
    assert "Circular interpolation" in _row(completed, 2025, 12)["Estimate Method"]


def test_partial_month_blends_actual_coverage_with_seasonal_estimate():
    summary = _summary({(2025, 2): 20, (2025, 3): 100, (2025, 4): 90})
    partial = (summary["Year"] == 2025) & (summary["Month"] == 2)
    summary.loc[partial, "Coverage %"] = 10.0
    summary.loc[partial, "Coverage Status"] = "Partial"
    windows = {"A": pd.period_range("2025-01", "2025-04", freq="M")}

    completed, notes = estimate_missing_months(summary, windows)
    feb = _row(completed, 2025, 2)
    feb_note = notes.loc[notes["Estimated month"] == "February 2025"].iloc[0]

    assert feb["Data Source"] == "Estimated"
    assert feb["Coverage Status"] == "Partial Estimated"
    assert feb["Total kWh"] > 20
    assert "Partial-month blend" in feb["Estimate Method"]
    assert feb_note["Coverage percentage"] == pytest.approx(10.0)


def test_estimated_category_totals_reconcile_to_total_kwh():
    summary = _summary({(2025, 3): 100, (2025, 4): 200})
    windows = {"A": pd.period_range("2025-02", "2025-04", freq="M")}

    completed, _ = estimate_missing_months(summary, windows)
    row = _row(completed, 2025, 2)

    assert row["Operating kWh"] + row["Non-Operating kWh"] == pytest.approx(row["Total kWh"])
    assert row["On-Peak Operating kWh"] + row["Off-Peak Operating kWh"] == pytest.approx(row["Operating kWh"])
    assert row["On-Peak Non-Operating kWh"] + row["Off-Peak Non-Operating kWh"] == pytest.approx(row["Non-Operating kWh"])


def test_equivalent_calendar_rotations_use_circular_neighbor_logic():
    august_window = {"A": pd.period_range("2025-08", "2026-07", freq="M")}
    january_window = {"A": pd.period_range("2025-01", "2025-12", freq="M")}
    august_completed, _ = estimate_missing_months(_summary({(2025, 11): 100, (2026, 7): 200}), august_window)
    january_completed, _ = estimate_missing_months(_summary({(2025, 4): 100, (2025, 12): 200}), january_window)

    assert _value(august_completed, 2025, 8) == pytest.approx(_value(january_completed, 2025, 1))


def test_existing_complete_actual_months_remain_unchanged():
    periods = pd.period_range("2024-06", "2025-05", freq="M")
    values = {(period.year, period.month): index * 10 for index, period in enumerate(periods, 1)}
    summary = _summary(values)
    completed, notes = estimate_missing_months(summary, {"A": periods})

    assert notes.empty
    assert set(completed["Data Source"]) == {"Actual"}
    for (year, month), value in values.items():
        assert _value(completed, year, month) == pytest.approx(value)


def test_no_missing_month_report_produces_identical_actual_values():
    periods = pd.period_range("2025-01", "2025-12", freq="M")
    values = {(period.year, period.month): 100 + period.month for period in periods}
    completed, notes = estimate_missing_months(_summary(values), {"A": periods})

    assert notes.empty
    assert list(_periods(completed)) == list(periods)
    assert [completed.loc[index, "Total kWh"] for index in completed.index] == [float(values[(period.year, period.month)]) for period in periods]


def test_synthetic_seasonal_validation_improves_leading_gap_error_against_known_actuals():
    known = {
        (2025, 8): 220,
        (2025, 9): 180,
        (2025, 10): 120,
        (2025, 11): 100,
        (2025, 12): 90,
        (2026, 1): 85,
        (2026, 2): 90,
        (2026, 3): 110,
        (2026, 4): 130,
        (2026, 5): 160,
        (2026, 6): 200,
        (2026, 7): 230,
    }
    observed = {period: value for period, value in known.items() if period not in {(2025, 8), (2025, 9), (2025, 10)}}
    windows = {"A": pd.period_range("2025-08", "2026-07", freq="M")}
    completed, _ = estimate_missing_months(_summary(observed), windows)

    actuals_by_period = {pd.Period(year=year, month=month, freq="M"): value for (year, month), value in known.items()}
    observed_by_period = {pd.Period(year=year, month=month, freq="M"): value for (year, month), value in observed.items()}
    missing = [pd.Period("2025-08"), pd.Period("2025-09"), pd.Period("2025-10")]
    old_error = sum(abs(_old_leading_linear_estimate(period, observed_by_period) - actuals_by_period[period]) for period in missing)
    new_error = sum(abs(_value(completed, period.year, period.month) - actuals_by_period[period]) for period in missing)

    assert new_error < old_error
