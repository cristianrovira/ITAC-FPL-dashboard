from datetime import time
from io import BytesIO

import pandas as pd
import pytest

from fpl_dashboard.extraction import ExtractedFile, apply_column_overrides, extract_excel_file, interval_detection
from fpl_dashboard.processing import process_files
from fpl_dashboard.validation import validate_files


def _xlsx_bytes(
    value_column: str,
    timestamp_style: str = "date_time",
    periods: int = 24,
    freq: str = "1h",
    values: list[float] | None = None,
    extra_values: dict[str, list[float]] | None = None,
) -> bytes:
    timestamps = pd.date_range("2025-01-01", periods=periods, freq=freq)
    values = values or list(range(1, periods + 1))
    if timestamp_style == "datetime":
        frame = pd.DataFrame({"DateTime": timestamps, value_column: values})
    elif timestamp_style == "timestamp":
        frame = pd.DataFrame({"Timestamp": timestamps, value_column: values})
    elif timestamp_style == "date_hour":
        frame = pd.DataFrame({"Date": timestamps.date, "Hour": [ts.hour for ts in timestamps], value_column: values})
    elif timestamp_style == "date_hour_1_24":
        frame = pd.DataFrame({"Date": timestamps.date, "Hour": [24 if ts.hour == 0 else ts.hour for ts in timestamps], value_column: values})
    elif timestamp_style == "date_numeric_time":
        frame = pd.DataFrame({"Date": timestamps.date, "Time": [ts.hour / 24 + ts.minute / 1440 for ts in timestamps], value_column: values})
    elif timestamp_style == "date_text_time":
        frame = pd.DataFrame({"Date": timestamps.date, "Time": [ts.strftime("%I:%M %p").lstrip("0") if hasattr(ts, "strftime") else str(ts) for ts in timestamps], value_column: values})
    elif timestamp_style == "date_full_plus_hour":
        frame = pd.DataFrame({"Date": timestamps, "Hour": [ts.hour for ts in timestamps], value_column: values})
    else:
        frame = pd.DataFrame({"Date": timestamps.date, "Time": timestamps.time, value_column: values})
    frame["Account Number"] = [1234567890] * len(frame)
    frame["Meter Number"] = [987654321] * len(frame)
    for column, column_values in (extra_values or {}).items():
        frame[column] = column_values
    output = BytesIO()
    frame.to_excel(output, index=False)
    return output.getvalue()


def _processed_total(item, interval_overrides=None):
    shifts = [{"days": list(range(7)), "start": time(0), "end": time(0), "active": True}]
    _, summary = process_files([item], shifts, interval_overrides=interval_overrides)
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
def test_interval_value_headers_produce_identical_results_for_hourly_data(value_column):
    item = extract_excel_file(_xlsx_bytes(value_column, freq="1h", periods=24), f"{value_column}.xlsx", "A")

    assert item.errors == []
    assert item.timestamp_column == "Date + Time"
    assert item.timestamp_detection_confidence == "High"
    assert item.demand_columns == [value_column]
    assert item.demand_detection_confidence == "High"
    assert item.detected_interval_hours == 1.0

    row = _processed_total(item)
    expected_kwh = sum(range(1, 25))
    assert row["Total kWh"] == expected_kwh
    assert row["Operating kWh"] == expected_kwh
    assert row["Non-Operating kWh"] == 0


@pytest.mark.parametrize("timestamp_style, expected_column", [("datetime", "DateTime"), ("timestamp", "Timestamp")])
def test_common_single_timestamp_headers_are_detected(timestamp_style, expected_column):
    item = extract_excel_file(_xlsx_bytes("Consumption_Recorded", timestamp_style=timestamp_style), "usage.xlsx", "A")

    assert item.errors == []
    assert item.timestamp_column == expected_column
    assert item.demand_columns == ["Consumption_Recorded"]
    assert item.detected_interval_hours == 1.0


@pytest.mark.parametrize(
    "minutes,freq",
    [(15, "15min"), (30, "30min"), (60, "1h")],
)
def test_standard_interval_detection(minutes, freq):
    timestamps = pd.date_range("2025-01-01", periods=12, freq=freq)
    detected = interval_detection(pd.Series(timestamps))

    assert detected.minutes == minutes
    assert detected.hours == minutes / 60
    assert detected.confidence == "High"


def test_separate_date_and_hour_columns_detect_hourly_timestamps():
    item = extract_excel_file(_xlsx_bytes("Consumption Recorded", timestamp_style="date_hour_1_24", periods=48), "hourly.xlsx", "A")

    assert item.errors == []
    assert item.timestamp_column == "Date + Hour"
    assert item.timestamp_valid_count == 48
    assert item.timestamp_unique_count == 48
    assert item.detected_interval_hours == 1.0
    assert item.interval_detection_confidence == "High"
    assert item.demand_columns == ["Consumption Recorded"]
    assert item.interval_value_unit == "energy_kwh"


def test_excel_numeric_time_fractions_are_combined_with_dates():
    item = extract_excel_file(_xlsx_bytes("Demand", timestamp_style="date_numeric_time", periods=24), "numeric-time.xlsx", "A")

    assert item.errors == []
    assert item.timestamp_column == "Date + Time"
    assert item.first_timestamp == pd.Timestamp("2025-01-01 00:00")
    assert item.last_timestamp == pd.Timestamp("2025-01-01 23:00")
    assert item.detected_interval_hours == 1.0


def test_text_time_values_are_combined_with_dates():
    item = extract_excel_file(_xlsx_bytes("Demand", timestamp_style="date_text_time", periods=24), "text-time.xlsx", "A")

    assert item.errors == []
    assert item.timestamp_column == "Date + Time"
    assert item.timestamp_valid_count == 24
    assert item.detected_interval_hours == 1.0


def test_missing_gaps_do_not_break_interval_detection():
    timestamps = pd.Series(pd.to_datetime(["2025-01-01 00:00", "2025-01-01 01:00", "2025-01-01 02:00", "2025-01-05 00:00", "2025-01-05 01:00"]))
    detected = interval_detection(timestamps)

    assert detected.minutes == 60
    assert detected.hours == 1.0


def test_duplicate_timestamps_do_not_break_interval_detection():
    timestamps = pd.Series(pd.to_datetime(["2025-01-01 00:00", "2025-01-01 00:00", "2025-01-01 01:00", "2025-01-01 02:00"]))
    detected = interval_detection(timestamps)

    assert detected.minutes == 60
    assert detected.hours == 1.0


def test_manual_one_hour_override_prevents_interval_error():
    frame = pd.DataFrame({"__timestamp__": [pd.Timestamp("2025-01-01")], "Demand": [10]})
    item = ExtractedFile(
        account="A",
        filename="single-row.xlsx",
        dataframe=frame,
        timestamp_column="Timestamp",
        demand_columns=["Demand"],
        month=1,
        year=2025,
        row_count=1,
        detected_interval_hours=None,
    )

    key = (item.account, item.filename)
    log, errors, _ = validate_files([item], interval_overrides={key: 1.0})

    assert errors == []
    assert log.iloc[0]["Detected interval"] == "1 hour"
    assert "data interval" not in str(log.iloc[0]["Warning or error message"]).lower()


def test_manual_interval_applies_to_every_uploaded_file():
    files = []
    overrides = {}
    for month in [1, 2]:
        frame = pd.DataFrame({"__timestamp__": [pd.Timestamp(2025, month, 1)], "Demand": [10]})
        item = ExtractedFile(
            account="A",
            filename=f"month-{month}.xlsx",
            dataframe=frame,
            timestamp_column="Timestamp",
            demand_columns=["Demand"],
            month=month,
            year=2025,
            row_count=1,
            detected_interval_hours=None,
        )
        files.append(item)
        overrides[(item.account, item.filename)] = 1.0

    log, errors, _ = validate_files(files, interval_overrides=overrides)

    assert errors == []
    assert set(log["Detected interval"]) == {"1 hour"}


def test_date_column_with_full_datetimes_is_used_directly():
    item = extract_excel_file(_xlsx_bytes("Demand", timestamp_style="date_full_plus_hour", periods=24), "date-full.xlsx", "A")

    assert item.errors == []
    assert item.timestamp_column == "Date"
    assert item.detected_interval_hours == 1.0


def test_multiple_equal_value_candidates_force_manual_choice_without_crashing():
    content = _xlsx_bytes("Usage", extra_values={"Load": list(range(1, 25))})
    item = extract_excel_file(content, "ambiguous.xlsx", "A")

    assert item.errors == []
    assert item.demand_columns == []
    assert item.demand_detection_confidence == "Low"
    assert {"Usage", "Load"}.issubset(set(item.demand_candidates))

    selected = apply_column_overrides(item, timestamp_column=item.timestamp_column, interval_value_columns=["Load"], interval_value_unit="power_kw")
    assert selected.errors == []
    assert selected.demand_columns == ["Load"]
    row = _processed_total(selected)
    assert row["Total kWh"] == sum(range(1, 25))
