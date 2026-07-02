from datetime import time

import pandas as pd

from fpl_dashboard.extraction import ExtractedFile
from fpl_dashboard.processing import process_files


def test_cross_calendar_month_rows_stay_in_assigned_reporting_month():
    frame = pd.DataFrame(
        {
            "Demand kW": [10, 20],
            "__timestamp__": pd.to_datetime(["2025-01-31 23:00", "2025-02-01 00:00"]),
        }
    )
    item = ExtractedFile(
        account="A",
        filename="january-period.xlsx",
        dataframe=frame,
        month=1,
        year=2025,
        demand_columns=["Demand kW"],
        detected_interval_hours=1.0,
    )
    shifts = [{"days": list(range(7)), "start": time(0), "end": time(0), "active": True}]
    intervals, summary = process_files([item], shifts)
    assert set(zip(intervals["Year"], intervals["Month"])) == {(2025, 1)}
    assert list(zip(summary["Year"], summary["Month"])) == [(2025, 1)]
    assert summary.loc[0, "Total kWh"] == 30


def test_summary_includes_official_dashboard_crossed_energy_buckets():
    frame = pd.DataFrame(
        {
            "Demand kW": [4, 8, 12],
            "__timestamp__": pd.to_datetime(["2025-07-01 13:00", "2025-07-01 19:00", "2025-07-05 13:00"]),
        }
    )
    item = ExtractedFile(
        account="A",
        filename="july.xlsx",
        dataframe=frame,
        month=7,
        year=2025,
        row_count=3,
        demand_columns=["Demand kW"],
        detected_interval_hours=1.0,
    )
    shifts = [{"days": [0, 1, 2, 3, 4], "start": time(7), "end": time(18), "active": True}]
    _, summary = process_files([item], shifts)

    row = summary.iloc[0]
    assert row["On-Peak Operating kWh"] == 4
    assert row["Off-Peak Operating kWh"] == 0
    assert row["On-Peak Non-Operating kWh"] == 8
    assert row["Off-Peak Non-Operating kWh"] == 12
    assert row["Coverage Status"] == "Partial"
