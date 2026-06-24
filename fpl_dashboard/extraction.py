"""Read and inspect uploaded FPL Excel workbooks."""

from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
from typing import Sequence

import numpy as np
import pandas as pd

from .utils import normalize_name


SUPPORTED_INTERVAL_MINUTES = np.array([15.0, 30.0, 60.0])


@dataclass
class ExtractedFile:
    account: str
    filename: str
    dataframe: pd.DataFrame | None = None
    timestamp_column: str | None = None
    demand_columns: list[str] = field(default_factory=list)
    numeric_columns: list[str] = field(default_factory=list)
    detected_interval_hours: float | None = None
    month: int | None = None
    year: int | None = None
    row_count: int = 0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.errors:
            return "Error"
        if self.warnings:
            return "Warning"
        return "Valid"


def detect_interval_hours(timestamps: pd.Series) -> tuple[float | None, str | None]:
    """Detect 15-, 30-, or 60-minute data from timestamp spacing."""
    clean = pd.to_datetime(timestamps, errors="coerce").dropna().sort_values().drop_duplicates()
    differences = clean.diff().dropna().dt.total_seconds().div(60)
    differences = differences[(differences > 0) & (differences <= 180)]
    if differences.empty:
        return None, "Not enough timestamps to detect the interval."

    median = float(differences.median())
    nearest = float(SUPPORTED_INTERVAL_MINUTES[np.argmin(np.abs(SUPPORTED_INTERVAL_MINUTES - median))])
    if abs(median - nearest) > 1.0:
        return None, f"Timestamp spacing ({median:g} minutes) is not a supported interval."

    consistency = float((np.abs(differences - nearest) <= 1.0).mean())
    warning = None
    if consistency < 0.9:
        warning = f"Only {consistency:.0%} of timestamp gaps match the detected interval."
    return nearest / 60.0, warning


def detect_month_year(timestamps: pd.Series) -> tuple[int | None, int | None, str | None]:
    """Assign one reporting month to a normal monthly billing-period file.

    FPL exports commonly cross a calendar-month boundary. A two-calendar-month
    span of up to 45 days is therefore normal and does not produce a warning.
    """
    clean = pd.to_datetime(timestamps, errors="coerce").dropna().sort_values()
    if clean.empty:
        return None, None, "No valid timestamps were found."

    periods = clean.dt.to_period("M")
    counts = periods.value_counts(sort=False)
    highest_count = counts.max()
    candidates = sorted(counts[counts == highest_count].index)
    midpoint_period = clean.iloc[len(clean) // 2].to_period("M")
    dominant = midpoint_period if midpoint_period in candidates else candidates[-1]

    span_days = (clean.iloc[-1] - clean.iloc[0]).total_seconds() / 86_400
    warning = None
    if len(counts) > 2 or span_days > 45:
        warning = (
            f"The file spans {len(counts)} calendar months and {span_days:.1f} days; "
            "verify that it represents one reporting period."
        )
    return int(dominant.month), int(dominant.year), warning

def _timestamp_candidates(columns: Sequence[object]) -> list[str]:
    preferred = ("datetime", "timestamp", "dateandtime", "intervaldatetime")
    normalized = {str(column): normalize_name(column) for column in columns}
    exact = [column for column, name in normalized.items() if name in preferred]
    fuzzy = [
        column for column, name in normalized.items()
        if column not in exact and ("datetime" in name or "timestamp" in name)
    ]
    return exact + fuzzy


def _parse_timestamps(frame: pd.DataFrame) -> tuple[pd.Series | None, str | None]:
    for column in _timestamp_candidates(frame.columns):
        parsed = pd.to_datetime(frame[column], errors="coerce")
        if parsed.notna().mean() >= 0.7:
            return parsed, column

    normalized = {normalize_name(column): str(column) for column in frame.columns}
    date_column = normalized.get("date") or normalized.get("intervaldate")
    time_column = normalized.get("time") or normalized.get("intervaltime")
    if date_column and time_column:
        combined = frame[date_column].astype(str).str.strip() + " " + frame[time_column].astype(str).str.strip()
        parsed = pd.to_datetime(combined, errors="coerce")
        if parsed.notna().mean() >= 0.7:
            return parsed, f"{date_column} + {time_column}"

    for column in frame.columns:
        name = normalize_name(column)
        if "date" not in name:
            continue
        parsed = pd.to_datetime(frame[column], errors="coerce")
        if parsed.notna().mean() >= 0.7:
            return parsed, str(column)
    return None, None


def detect_demand_columns(frame: pd.DataFrame, timestamp_column: str | None = None) -> tuple[list[str], list[str]]:
    numeric_columns: list[str] = []
    demand_columns: list[str] = []
    timestamp_parts = set((timestamp_column or "").split(" + "))
    for column in frame.columns:
        column = str(column)
        if column in timestamp_parts:
            continue
        numeric = pd.to_numeric(frame[column], errors="coerce")
        if numeric.notna().mean() < 0.7:
            continue
        numeric_columns.append(column)
        name = normalize_name(column)
        is_energy = "kwh" in name or "energy" in name
        if not is_energy and ("demand" in name or "kw" in name):
            demand_columns.append(column)
    return demand_columns, numeric_columns


def _read_best_sheet(content: bytes) -> tuple[pd.DataFrame, pd.Series | None, str | None]:
    errors: list[str] = []
    for header_row in (3, 0, 1, 2, 4, 5):
        try:
            frame = pd.read_excel(BytesIO(content), header=header_row)
        except Exception as exc:  # Try alternate header positions before failing.
            errors.append(str(exc))
            continue
        frame = frame.dropna(how="all").dropna(axis=1, how="all")
        if frame.empty:
            continue
        timestamps, timestamp_column = _parse_timestamps(frame)
        if timestamps is not None:
            return frame, timestamps, timestamp_column
    if errors:
        raise ValueError(f"Excel file could not be read: {errors[0]}")
    raise ValueError("No usable worksheet with a timestamp column was found.")


def extract_excel_file(content: bytes, filename: str, account: str) -> ExtractedFile:
    result = ExtractedFile(account=account, filename=filename)
    try:
        frame, timestamps, timestamp_column = _read_best_sheet(content)
    except Exception as exc:
        result.errors.append(str(exc))
        return result

    result.dataframe = frame.copy()
    result.dataframe["__timestamp__"] = timestamps
    result.timestamp_column = timestamp_column
    result.row_count = len(frame)
    if result.row_count < 24:
        result.warnings.append("The file has fewer than 24 data rows.")

    valid_timestamp_ratio = float(timestamps.notna().mean())
    if valid_timestamp_ratio < 0.95:
        result.warnings.append(f"{1 - valid_timestamp_ratio:.1%} of rows have invalid timestamps.")

    result.month, result.year, month_warning = detect_month_year(timestamps)
    if month_warning:
        result.warnings.append(month_warning)
    if result.month is None:
        result.errors.append("The reporting month could not be identified.")

    result.detected_interval_hours, interval_warning = detect_interval_hours(timestamps)
    if interval_warning:
        result.warnings.append(interval_warning)

    result.demand_columns, result.numeric_columns = detect_demand_columns(frame, timestamp_column)
    if not result.demand_columns:
        result.warnings.append("A demand column was not detected automatically; select one manually.")
    return result
