import pandas as pd

from fpl_dashboard.extraction import ExtractedFile
from fpl_dashboard.validation import validate_files


def _file(name, timestamps, assigned_year, assigned_month):
    frame = pd.DataFrame({"Demand kW": [10] * len(timestamps), "__timestamp__": timestamps})
    return ExtractedFile(
        account="A",
        filename=name,
        dataframe=frame,
        month=assigned_month,
        year=assigned_year,
        demand_columns=["Demand kW"],
        detected_interval_hours=1.0,
    )


def test_adjacent_billing_files_may_share_calendar_month_without_duplicate_error():
    january_period = _file(
        "january-period.xlsx",
        pd.to_datetime(["2025-01-15 00:00", "2025-02-14 23:00"]),
        2025,
        1,
    )
    february_period = _file(
        "february-period.xlsx",
        pd.to_datetime(["2025-02-15 00:00", "2025-03-14 23:00"]),
        2025,
        2,
    )
    log, errors, _ = validate_files([january_period, february_period])
    assert not any("Duplicate" in error for error in errors)
    assert set(log["Status"]) == {"Valid"}
