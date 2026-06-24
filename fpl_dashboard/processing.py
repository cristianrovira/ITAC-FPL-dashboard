"""Normalize interval readings and build actual monthly summaries."""

from __future__ import annotations

from typing import Mapping, Sequence

import numpy as np
import pandas as pd

from .classification import classify_on_peak, classify_operating
from .extraction import ExtractedFile
from .validation import FileKey, selected_demand_columns


ENERGY_COLUMNS = [
    "Total kWh",
    "Operating kWh",
    "Non-Operating kWh",
    "On-Peak kWh",
    "Off-Peak kWh",
    "Weekend kWh",
    "Overnight kWh",
]
DEMAND_COLUMNS = [
    "Peak Demand kW",
    "Operating Demand kW",
    "Non-Operating Demand kW",
    "On-Peak Demand kW",
    "Off-Peak Demand kW",
]


def _masked_sum(frame: pd.DataFrame, mask: pd.Series) -> float:
    return float(frame.loc[mask, "Interval kWh"].sum())


def _masked_max(frame: pd.DataFrame, mask: pd.Series) -> float:
    values = frame.loc[mask, "Demand kW"]
    return float(values.max()) if not values.empty else 0.0


def normalize_file(
    item: ExtractedFile,
    demand_columns: Sequence[str],
    interval_hours: float,
) -> pd.DataFrame:
    if item.dataframe is None:
        raise ValueError(f"{item.filename} has no readable data.")
    frame = item.dataframe.copy()
    numeric = frame[list(demand_columns)].apply(pd.to_numeric, errors="coerce")
    demand = numeric.sum(axis=1, min_count=1)
    normalized = pd.DataFrame(
        {
            "Timestamp": pd.to_datetime(frame["__timestamp__"], errors="coerce"),
            "Demand kW": demand,
        }
    ).dropna(subset=["Timestamp", "Demand kW"])
    normalized = normalized.sort_values("Timestamp").drop_duplicates(subset=["Timestamp"], keep="last")
    normalized["Interval Hours"] = float(interval_hours)
    normalized["Interval kWh"] = normalized["Demand kW"] * normalized["Interval Hours"]
    normalized["Account"] = item.account
    normalized["Source File"] = item.filename
    normalized["Year"] = int(item.year)
    normalized["Month"] = int(item.month)
    return normalized


def classify_intervals(frame: pd.DataFrame, shifts: Sequence[Mapping[str, object]]) -> pd.DataFrame:
    result = frame.copy()
    result["Operating"] = classify_operating(result["Timestamp"], shifts)
    result["On-Peak"] = classify_on_peak(result["Timestamp"])
    result["Weekend"] = result["Timestamp"].dt.weekday >= 5
    result["Overnight"] = (result["Timestamp"].dt.hour < 6) | (result["Timestamp"].dt.hour >= 22)
    if "Year" not in result or "Month" not in result:
        result["Year"] = result["Timestamp"].dt.year.astype(int)
        result["Month"] = result["Timestamp"].dt.month.astype(int)
    return result


def summarize_actual_intervals(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for (account, year, month), group in frame.groupby(["Account", "Year", "Month"], sort=True):
        operating = group["Operating"]
        on_peak = group["On-Peak"]
        peak_index = group["Demand kW"].idxmax()
        row = {
            "Account": account,
            "Year": int(year),
            "Month": int(month),
            "Total kWh": float(group["Interval kWh"].sum()),
            "Peak Demand kW": float(group["Demand kW"].max()),
            "Operating kWh": _masked_sum(group, operating),
            "Non-Operating kWh": _masked_sum(group, ~operating),
            "Operating Demand kW": _masked_max(group, operating),
            "Non-Operating Demand kW": _masked_max(group, ~operating),
            "On-Peak kWh": _masked_sum(group, on_peak),
            "Off-Peak kWh": _masked_sum(group, ~on_peak),
            "On-Peak Demand kW": _masked_max(group, on_peak),
            "Off-Peak Demand kW": _masked_max(group, ~on_peak),
            "Weekend kWh": _masked_sum(group, group["Weekend"]),
            "Overnight kWh": _masked_sum(group, group["Overnight"]),
            "Peak During Non-Operating": not bool(group.loc[peak_index, "Operating"]),
            "Data Source": "Actual",
            "Estimate Method": "Actual uploaded interval file",
            "Confidence": "Normal",
        }
        row["Non-Operating %"] = 100 * row["Non-Operating kWh"] / row["Total kWh"] if row["Total kWh"] else 0.0
        rows.append(row)
    return pd.DataFrame(rows)


def process_files(
    files: Sequence[ExtractedFile],
    shifts: Sequence[Mapping[str, object]],
    demand_selections: Mapping[FileKey, Sequence[str]] | None = None,
    interval_overrides: Mapping[FileKey, float] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    normalized_files: list[pd.DataFrame] = []
    for item in files:
        key = (item.account, item.filename)
        demand_columns = selected_demand_columns(item, demand_selections)
        interval = interval_overrides.get(key) if interval_overrides and key in interval_overrides else item.detected_interval_hours
        if not demand_columns or interval is None or item.errors:
            continue
        normalized_files.append(normalize_file(item, demand_columns, float(interval)))
    if not normalized_files:
        raise ValueError("No valid interval data is available to process.")
    interval_data = classify_intervals(pd.concat(normalized_files, ignore_index=True), shifts)
    return interval_data, summarize_actual_intervals(interval_data)


def find_potential_issues(summary: pd.DataFrame) -> list[str]:
    issues: list[str] = []
    total = float(summary["Total kWh"].sum())
    non_operating = float(summary["Non-Operating kWh"].sum())
    weekend = float(summary["Weekend kWh"].sum())
    overnight = float(summary["Overnight kWh"].sum())
    if total and non_operating / total >= 0.30:
        issues.append(f"Non-operating consumption is {non_operating / total:.1%} of annual usage.")
    if total and weekend / total >= 0.20:
        issues.append(f"Weekend consumption is {weekend / total:.1%} of annual usage.")
    if total and overnight / total >= 0.20:
        issues.append(f"Overnight consumption is {overnight / total:.1%} of annual usage.")
    peak_months = summary.loc[
        (summary["Data Source"] == "Actual") & summary["Peak During Non-Operating"].fillna(False),
        ["Account", "Month", "Year"],
    ]
    for row in peak_months.itertuples(index=False):
        issues.append(f"Peak demand occurred during non-operating hours for {row.Account} in {pd.Timestamp(row.Year, row.Month, 1).strftime('%B %Y')}.")
    estimated_count = int((summary["Data Source"] == "Estimated").sum())
    if estimated_count:
        issues.append(f"{estimated_count} account-month(s) were estimated because interval files were missing.")
    return issues or ["No simple threshold-based issues were detected. Review the charts for site-specific patterns."]
