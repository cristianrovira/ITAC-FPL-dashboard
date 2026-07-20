"""Normalize interval readings and build actual monthly summaries."""

from __future__ import annotations

from typing import Mapping, Sequence

import numpy as np
import pandas as pd

from .classification import (
    apply_timestamp_alignment,
    classify_idle_load,
    classify_on_peak,
    classify_operating,
    idle_load_threshold,
    is_around_the_clock_schedule,
    normalize_classification_options,
)
from .extraction import ExtractedFile
from .report_period import coverage_by_account_period
from .validation import FileKey, selected_demand_columns


ENERGY_COLUMNS = [
    "Total kWh",
    "Operating kWh",
    "Non-Operating kWh",
    "On-Peak kWh",
    "Off-Peak kWh",
    "On-Peak Operating kWh",
    "Off-Peak Operating kWh",
    "On-Peak Non-Operating kWh",
    "Off-Peak Non-Operating kWh",
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
    interval_values = numeric.sum(axis=1, min_count=1)
    interval_hours = float(interval_hours)
    if item.interval_value_unit == "energy_kwh":
        interval_kwh = interval_values
        demand = interval_values / interval_hours if interval_hours else interval_values
    else:
        demand = interval_values
        interval_kwh = interval_values * interval_hours
    normalized = pd.DataFrame(
        {
            "Timestamp": pd.to_datetime(frame["__timestamp__"], errors="coerce"),
            "Demand kW": demand,
            "Interval kWh": interval_kwh,
        }
    ).dropna(subset=["Timestamp", "Demand kW"])
    normalized = normalized.sort_values("Timestamp").drop_duplicates(subset=["Timestamp"], keep="last")
    normalized["Interval Hours"] = interval_hours
    normalized["Account"] = item.account
    normalized["Source File"] = item.filename
    normalized["Year"] = int(item.year)
    normalized["Month"] = int(item.month)
    return normalized


def classify_intervals(
    frame: pd.DataFrame,
    shifts: Sequence[Mapping[str, object]],
    classification_options: Mapping[str, object] | None = None,
) -> pd.DataFrame:
    result = frame.copy()
    options = normalize_classification_options(classification_options)
    result["Classification Timestamp"] = apply_timestamp_alignment(
        result["Timestamp"],
        result.get("Interval Hours", 0.0),
        str(options["timestamp_alignment"]),
    )

    mode = str(options["operating_mode"])
    if mode == "idle_load":
        result["Operating"] = False
        result["Idle Threshold kW"] = np.nan
        for account, account_index in result.groupby("Account").groups.items():
            demand = result.loc[account_index, "Demand kW"]
            threshold = idle_load_threshold(demand, float(options["idle_quantile"]))
            result.loc[account_index, "Idle Threshold kW"] = threshold
            result.loc[account_index, "Operating"] = classify_idle_load(demand, float(options["idle_quantile"])).values
        result["Operating"] = result["Operating"].astype(bool)
    else:
        if is_around_the_clock_schedule(shifts):
            scheduled = pd.Series(True, index=result.index, dtype=bool)
        else:
            scheduled = classify_operating(result["Classification Timestamp"], shifts)
        if mode == "hybrid":
            idle = pd.Series(False, index=result.index, dtype=bool)
            result["Idle Threshold kW"] = np.nan
            for account, account_index in result.groupby("Account").groups.items():
                demand = result.loc[account_index, "Demand kW"]
                threshold = idle_load_threshold(demand, float(options["idle_quantile"]))
                result.loc[account_index, "Idle Threshold kW"] = threshold
                idle.loc[account_index] = classify_idle_load(demand, float(options["idle_quantile"])).values
            result["Operating"] = (scheduled | idle).astype(bool)
        else:
            result["Operating"] = scheduled.astype(bool)
            result["Idle Threshold kW"] = np.nan

    result["Classification Mode"] = mode
    result["On-Peak"] = classify_on_peak(result["Classification Timestamp"], str(options["on_peak_rule"]))
    result["Weekend"] = result["Classification Timestamp"].dt.weekday >= 5
    result["Overnight"] = (result["Classification Timestamp"].dt.hour < 6) | (result["Classification Timestamp"].dt.hour >= 22)
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
            "Total Rows": int(len(group)),
            "Operating Rows": int(operating.sum()),
            "Not Operating Rows": int((~operating).sum()),
            "Total kWh": float(group["Interval kWh"].sum()),
            "Peak Demand kW": float(group["Demand kW"].max()),
            "Operating kWh": _masked_sum(group, operating),
            "Non-Operating kWh": _masked_sum(group, ~operating),
            "Operating Demand kW": _masked_max(group, operating),
            "Non-Operating Demand kW": _masked_max(group, ~operating),
            "On-Peak kWh": _masked_sum(group, on_peak),
            "Off-Peak kWh": _masked_sum(group, ~on_peak),
            "On-Peak Operating kWh": _masked_sum(group, operating & on_peak),
            "Off-Peak Operating kWh": _masked_sum(group, operating & ~on_peak),
            "On-Peak Non-Operating kWh": _masked_sum(group, ~operating & on_peak),
            "Off-Peak Non-Operating kWh": _masked_sum(group, ~operating & ~on_peak),
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
    classification_options: Mapping[str, object] | None = None,
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
    interval_data = classify_intervals(
        pd.concat(normalized_files, ignore_index=True),
        shifts,
        classification_options,
    )
    summary = summarize_actual_intervals(interval_data)
    coverage = coverage_by_account_period(files, interval_overrides)
    uploaded_rows: dict[tuple[str, int, int], int] = {}
    for item in files:
        if item.errors or item.year is None or item.month is None:
            continue
        key = (item.account, int(item.year), int(item.month))
        uploaded_rows[key] = uploaded_rows.get(key, 0) + int(item.row_count or 0)

    coverage_values = []
    coverage_statuses = []
    row_counts = []
    expected_rows = []
    for row in summary.itertuples(index=False):
        period = pd.Period(year=int(row.Year), month=int(row.Month), freq="M")
        ratio = coverage.get((row.Account, period), 1.0)
        coverage_values.append(round(float(ratio) * 100, 1))
        coverage_statuses.append("Partial" if ratio < 0.85 else "Complete")
        row_count = uploaded_rows.get((row.Account, int(row.Year), int(row.Month)), 0)
        row_counts.append(row_count)
        expected_rows.append(round(row_count / ratio) if ratio else 0)
    summary["Coverage %"] = coverage_values
    summary["Coverage Status"] = coverage_statuses
    summary["Uploaded Row Count"] = row_counts
    summary["Expected Row Count"] = expected_rows
    return interval_data, summary


def daily_hourly_classification_breakdown(interval_data: pd.DataFrame) -> pd.DataFrame:
    """Return actual interval classification diagnostics by day of week and hour."""
    if interval_data.empty:
        return pd.DataFrame()
    frame = interval_data.copy()
    frame["Month / Year"] = pd.to_datetime(
        dict(year=frame["Year"].astype(int), month=frame["Month"].astype(int), day=1)
    ).dt.strftime("%B %Y")
    frame["Day of Week"] = frame["Classification Timestamp"].dt.day_name()
    frame["Weekday Number"] = frame["Classification Timestamp"].dt.weekday
    frame["Hour"] = frame["Classification Timestamp"].dt.hour
    frame["Operating kWh"] = frame["Interval kWh"].where(frame["Operating"], 0.0)
    frame["Not Operating kWh"] = frame["Interval kWh"].where(~frame["Operating"], 0.0)
    frame["On-Peak Operating kWh"] = frame["Interval kWh"].where(frame["Operating"] & frame["On-Peak"], 0.0)
    frame["Off-Peak Operating kWh"] = frame["Interval kWh"].where(frame["Operating"] & ~frame["On-Peak"], 0.0)
    frame["On-Peak Not Operating kWh"] = frame["Interval kWh"].where(~frame["Operating"] & frame["On-Peak"], 0.0)
    frame["Off-Peak Not Operating kWh"] = frame["Interval kWh"].where(~frame["Operating"] & ~frame["On-Peak"], 0.0)
    grouped = frame.groupby(["Account", "Year", "Month", "Month / Year", "Weekday Number", "Day of Week", "Hour"], sort=True).agg(
        **{
            "Total Rows": ("Interval kWh", "count"),
            "Operating Rows": ("Operating", "sum"),
            "Total kWh": ("Interval kWh", "sum"),
            "Operating kWh": ("Operating kWh", "sum"),
            "Not Operating kWh": ("Not Operating kWh", "sum"),
            "On-Peak Operating kWh": ("On-Peak Operating kWh", "sum"),
            "Off-Peak Operating kWh": ("Off-Peak Operating kWh", "sum"),
            "On-Peak Not Operating kWh": ("On-Peak Not Operating kWh", "sum"),
            "Off-Peak Not Operating kWh": ("Off-Peak Not Operating kWh", "sum"),
        }
    ).reset_index()
    grouped["Not Operating Rows"] = grouped["Total Rows"] - grouped["Operating Rows"]
    ordered = [
        "Account",
        "Year",
        "Month",
        "Month / Year",
        "Weekday Number",
        "Day of Week",
        "Hour",
        "Total Rows",
        "Operating Rows",
        "Not Operating Rows",
        "Total kWh",
        "Operating kWh",
        "Not Operating kWh",
        "On-Peak Operating kWh",
        "Off-Peak Operating kWh",
        "On-Peak Not Operating kWh",
        "Off-Peak Not Operating kWh",
    ]
    return grouped[ordered]


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
