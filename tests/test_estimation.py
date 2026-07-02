import pandas as pd

from fpl_dashboard.estimation import estimate_missing_months
from fpl_dashboard.processing import DEMAND_COLUMNS, ENERGY_COLUMNS


def _summary(period_values):
    rows = []
    for (year, month), value in period_values.items():
        row = {
            "Account": "A",
            "Year": year,
            "Month": month,
            "Data Source": "Actual",
            "Estimate Method": "Actual uploaded interval file",
            "Confidence": "Normal",
            "Peak During Non-Operating": False,
            "Non-Operating %": 25.0,
        }
        for column in ENERGY_COLUMNS + DEMAND_COLUMNS:
            row[column] = float(value)
        rows.append(row)
    return pd.DataFrame(rows)


def _value(completed, year, month):
    match = (completed["Year"] == year) & (completed["Month"] == month)
    return completed.loc[match, "Total kWh"].iloc[0]


def test_estimate_one_missing_month_between_actual_months():
    completed, _ = estimate_missing_months(_summary({(2025, 2): 20, (2025, 4): 40}))
    assert _value(completed, 2025, 3) == 30
    match = (completed["Year"] == 2025) & (completed["Month"] == 3)
    assert completed.loc[match, "Data Source"].iloc[0] == "Estimated"


def test_estimate_multiple_consecutive_months():
    completed, _ = estimate_missing_months(_summary({(2025, 2): 20, (2025, 5): 50}))
    assert _value(completed, 2025, 3) == 30
    assert _value(completed, 2025, 4) == 40


def test_missing_january_uses_december_and_february_across_years():
    completed, _ = estimate_missing_months(_summary({(2024, 12): 120, (2025, 2): 20}))
    assert _value(completed, 2025, 1) == 70


def test_missing_december_uses_november_and_january_across_years():
    completed, _ = estimate_missing_months(_summary({(2024, 11): 110, (2025, 1): 10}))
    assert _value(completed, 2024, 12) == 60


def test_complete_cross_year_window_needs_no_estimates():
    periods = pd.period_range("2024-06", "2025-05", freq="M")
    summary = _summary({(period.year, period.month): index for index, period in enumerate(periods, 1)})
    completed, notes = estimate_missing_months(summary)
    assert len(completed) == 12
    assert set(completed["Data Source"]) == {"Actual"}
    assert notes.empty


def test_actual_and_estimated_labels_are_preserved():
    completed, notes = estimate_missing_months(_summary({(2025, 1): 10, (2025, 2): 20}))
    actual = (completed["Year"] == 2025) & (completed["Month"] == 1)
    assert completed.loc[actual, "Data Source"].iloc[0] == "Actual"
    assert (completed["Data Source"] == "Estimated").any()
    assert not notes.empty


def test_single_month_carry_forward_marks_far_months_very_low_confidence():
    completed, _ = estimate_missing_months(_summary({(2025, 6): 60}))
    estimated = completed[completed["Data Source"] == "Estimated"]
    assert (estimated["Total kWh"] == 60).all()
    assert set(estimated["Confidence"]).issubset({"Low", "Very Low"})
    assert "Very Low" in set(estimated["Confidence"])


def test_leading_missing_months_use_first_actual_trend_instead_of_flat_copy():
    completed, _ = estimate_missing_months(_summary({(2025, 10): 70, (2025, 11): 60}))
    assert _value(completed, 2025, 9) == 80
    assert _value(completed, 2025, 8) == 90
    september = (completed["Year"] == 2025) & (completed["Month"] == 9)
    assert "Trend extrapolated backward" in completed.loc[september, "Estimate Method"].iloc[0]


def test_selected_report_window_controls_output_months():
    windows = {"A": pd.period_range("2025-03", "2026-02", freq="M")}
    completed, _ = estimate_missing_months(_summary({(2025, 10): 70, (2025, 11): 60}), windows)
    periods = pd.PeriodIndex.from_fields(year=completed["Year"], month=completed["Month"], freq="M")
    assert list(periods) == list(windows["A"])


def test_partial_month_is_scaled_and_excluded_from_trend_anchors():
    summary = _summary({(2025, 2): 20, (2025, 3): 100, (2025, 4): 90})
    partial = (summary["Year"] == 2025) & (summary["Month"] == 2)
    summary.loc[partial, "Coverage %"] = 10.0
    summary.loc[partial, "Coverage Status"] = "Partial"
    summary.loc[~partial, "Coverage %"] = 100.0
    summary.loc[~partial, "Coverage Status"] = "Complete"
    windows = {"A": pd.period_range("2025-01", "2025-04", freq="M")}

    completed, notes = estimate_missing_months(summary, windows)

    assert _value(completed, 2025, 2) == 200
    assert _value(completed, 2025, 1) == 120
    feb = (completed["Year"] == 2025) & (completed["Month"] == 2)
    assert completed.loc[feb, "Data Source"].iloc[0] == "Estimated"
    assert "Partial-month scale-up" in completed.loc[feb, "Estimate Method"].iloc[0]
    jan = (completed["Year"] == 2025) & (completed["Month"] == 1)
    assert "March 2025 and April 2025" in completed.loc[jan, "Estimate Method"].iloc[0]
    assert len(notes) == 2
