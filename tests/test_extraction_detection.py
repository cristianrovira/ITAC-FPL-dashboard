from datetime import time
from io import BytesIO

import pandas as pd
import pytest

from fpl_dashboard.extraction import apply_column_overrides, extract_excel_file
from fpl_dashboard.processing import process_files


def _xlsx_bytes(value_column: str, timestamp_style: str = "date_time", extra_values: dict[str, list[float]] | None = None) -> bytes:
    timestamps = pd.date_range("2025-01-01", periods=96, freq="15min")
    values = list(range(1, 97))
    if timestamp_style == "datetime":
        frame = pd.DataFrame({"DateTime": timestamps, value_column: values})
    elif timestamp_style == "timestamp":
        frame = pd.DataFrame({"Timestamp": timestamps, value_column: values})
    else:
        frame = pd.DataFrame({"Date": timestamps.date, "Time": timestamps.time, value_column: values})
    frame["Account Number"] = [1234567890] * len(frame)
    frame["Meter Number"] = [987654321] * len(frame)
    for column, column_values in (extra_values or {}).items():
        frame[column] = column_values
    output = BytesIO()
    frame.to_excel(output, index=False)
    return output.getvalue()


def _processed_total(item):
    shifts = [{"days": list(range(7)), "start": time(0), "end": time(0), "active": True}]
    _, summary = process_files([item], shifts)
    return summary.iloc[0]


@pytest.mark.parametrize(
    "value_column",
    [
        "Demand (kW)",
        "Demand",
        "Consumption Recorded",
        "Usage",
        "kW",
        "Energy",
        "Interval Load",
    ],
)
def test_interval_value_headers_produce_identical_results(value_column):
    item = extract_excel_file(_xlsx_bytes(value_column), f"{value_column}.xlsx", "A")

    assert item.errors == []
    assert item.timestamp_column == "Date + Time"
    assert item.timestamp_detection_confidence == "High"
    assert item.demand_columns == [value_column]
    assert item.demand_detection_confidence == "High"

    row = _processed_total(item)
    expected_kwh = sum(range(1, 97)) * 0.25
    assert row["Total kWh"] == expected_kwh
    assert row["Operating kWh"] == expected_kwh
    assert row["Non-Operating kWh"] == 0


@pytest.mark.parametrize("timestamp_style, expected_column", [("datetime", "DateTime"), ("timestamp", "Timestamp")])
def test_common_single_timestamp_headers_are_detected(timestamp_style, expected_column):
    item = extract_excel_file(_xlsx_bytes("Consumption_Recorded", timestamp_style=timestamp_style), "usage.xlsx", "A")

    assert item.errors == []
    assert item.timestamp_column == expected_column
    assert item.demand_columns == ["Consumption_Recorded"]
    assert item.detected_interval_hours == 0.25


def test_multiple_equal_value_candidates_force_manual_choice_without_crashing():
    content = _xlsx_bytes("Usage", extra_values={"Load": list(range(1, 97))})
    item = extract_excel_file(content, "ambiguous.xlsx", "A")

    assert item.errors == []
    assert item.demand_columns == []
    assert item.demand_detection_confidence == "Low"
    assert {"Usage", "Load"}.issubset(set(item.demand_candidates))

    selected = apply_column_overrides(item, timestamp_column=item.timestamp_column, interval_value_columns=["Load"])
    assert selected.errors == []
    assert selected.demand_columns == ["Load"]
    row = _processed_total(selected)
    assert row["Total kWh"] == sum(range(1, 97)) * 0.25
