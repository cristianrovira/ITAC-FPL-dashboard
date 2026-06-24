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
