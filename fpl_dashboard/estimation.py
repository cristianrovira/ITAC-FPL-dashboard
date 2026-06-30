"""Estimate missing reporting-month summaries without fabricating interval data."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np
import pandas as pd

from .processing import DEMAND_COLUMNS, ENERGY_COLUMNS


NUMERIC_ESTIMATE_COLUMNS = ENERGY_COLUMNS + DEMAND_COLUMNS
NOTE_COLUMNS = [
    "Account number",
    "Year",
    "Uploaded months",
    "Missing months",
    "Estimated months",
    "Estimate method",
    "Confidence level",
    "Notes or warnings",
]


def _with_period(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["Period"] = pd.PeriodIndex.from_fields(
        year=result["Year"].astype(int),
        month=result["Month"].astype(int),
        freq="M",
    )
    return result


def _coerce_windows(windows: Mapping[str, Sequence[pd.Period]] | None) -> dict[str, pd.PeriodIndex]:
    if not windows:
        return {}
    return {
        str(account): pd.PeriodIndex([pd.Period(period, freq="M") for period in window], freq="M")
        for account, window in windows.items()
    }


def detect_missing_months(
    summary: pd.DataFrame,
    windows: Mapping[str, Sequence[pd.Period]] | None = None,
) -> dict[str, list[pd.Period]]:
    result: dict[str, list[pd.Period]] = {}
    if summary.empty:
        return result
    explicit_windows = _coerce_windows(windows)
    for account, group in _with_period(summary).groupby("Account"):
        actual = set(group["Period"])
        window = explicit_windows.get(str(account), pd.period_range(end=max(actual), periods=12, freq="M"))
        result[str(account)] = [period for period in window if period not in actual]
    return result


def _period_names(periods: Sequence[pd.Period]) -> str:
    return ", ".join(pd.Period(period, freq="M").strftime("%B %Y") for period in periods)


def _numeric_value(group: pd.DataFrame, period: pd.Period, column: str) -> float | None:
    value = pd.to_numeric(pd.Series([group.loc[period, column]]), errors="coerce").iloc[0]
    return float(value) if pd.notna(value) else None


def _set_numeric(row: pd.Series, column: str, value: float | None) -> None:
    row[column] = float(max(value, 0.0)) if value is not None and pd.notna(value) else np.nan


def _single_month_row(group: pd.DataFrame, target: pd.Period, actual_periods: list[pd.Period]) -> tuple[pd.Series, str, str]:
    source = actual_periods[0]
    confidence = "Very Low" if abs(source.ordinal - target.ordinal) > 2 else "Low"
    return group.loc[source].copy(), f"Single-month carry-forward from {source.strftime('%B %Y')}", confidence


def _linear_interpolated_row(
    group: pd.DataFrame,
    target: pd.Period,
    left_period: pd.Period,
    right_period: pd.Period,
) -> tuple[pd.Series, str, str]:
    distance = right_period.ordinal - left_period.ordinal
    weight = (target.ordinal - left_period.ordinal) / distance
    row = group.loc[left_period].copy()
    for column in NUMERIC_ESTIMATE_COLUMNS:
        if column not in group.columns:
            continue
        left = _numeric_value(group, left_period, column)
        right = _numeric_value(group, right_period, column)
        _set_numeric(row, column, left + (right - left) * weight if left is not None and right is not None else None)
    gap = distance - 1
    confidence = "Normal" if gap <= 2 else "Low"
    if gap == 1:
        method = f"Interpolated from {left_period.strftime('%B %Y')} and {right_period.strftime('%B %Y')}"
    else:
        method = f"Linear interpolation from {left_period.strftime('%B %Y')} to {right_period.strftime('%B %Y')}"
    return row, method, confidence


def _trend_extrapolated_row(
    group: pd.DataFrame,
    target: pd.Period,
    actual_periods: list[pd.Period],
) -> tuple[pd.Series, str, str]:
    if len(actual_periods) == 1:
        return _single_month_row(group, target, actual_periods)

    if target < actual_periods[0]:
        anchor = actual_periods[0]
        neighbor = actual_periods[1]
        steps = anchor.ordinal - target.ordinal
        direction = "backward"
    else:
        anchor = actual_periods[-1]
        neighbor = actual_periods[-2]
        steps = target.ordinal - anchor.ordinal
        direction = "forward"

    distance = abs(anchor.ordinal - neighbor.ordinal) or 1
    row = group.loc[anchor].copy()
    for column in NUMERIC_ESTIMATE_COLUMNS:
        if column not in group.columns:
            continue
        anchor_value = _numeric_value(group, anchor, column)
        neighbor_value = _numeric_value(group, neighbor, column)
        if anchor_value is None or neighbor_value is None:
            _set_numeric(row, column, anchor_value)
            continue
        monthly_change = (anchor_value - neighbor_value) / distance
        estimate = anchor_value + monthly_change * steps
        if anchor_value > 0:
            estimate = min(estimate, anchor_value * (1 + 0.35 * steps))
        _set_numeric(row, column, estimate)

    confidence = "Very Low" if steps >= 3 else "Low"
    method = (
        f"Trend extrapolated {direction} from {anchor.strftime('%B %Y')} "
        f"and {neighbor.strftime('%B %Y')}"
    )
    return row, method, confidence


def _estimate_row(
    group: pd.DataFrame,
    target: pd.Period,
    actual_periods: list[pd.Period],
) -> tuple[pd.Series, str, str]:
    if len(actual_periods) == 1:
        return _single_month_row(group, target, actual_periods)

    before = [period for period in actual_periods if period < target]
    after = [period for period in actual_periods if period > target]
    if before and after:
        return _linear_interpolated_row(group, target, max(before), min(after))

    return _trend_extrapolated_row(group, target, actual_periods)


def estimate_missing_months(
    summary: pd.DataFrame,
    windows: Mapping[str, Sequence[pd.Period]] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return one 12-month view per account and estimation notes."""
    if summary.empty:
        return summary.copy(), pd.DataFrame(columns=NOTE_COLUMNS)

    completed_groups: list[pd.DataFrame] = []
    notes: list[dict[str, object]] = []
    period_summary = _with_period(summary)
    explicit_windows = _coerce_windows(windows)

    for account, account_group in period_summary.groupby("Account", sort=True):
        account_group = account_group.sort_values("Period").drop_duplicates("Period", keep="last")
        account_group = account_group.set_index("Period")
        actual_periods = sorted(account_group.index.tolist())
        if not actual_periods:
            continue

        window = explicit_windows.get(str(account), pd.period_range(end=max(actual_periods), periods=12, freq="M"))
        missing_periods = [period for period in window if period not in account_group.index]
        uploaded_names = _period_names(actual_periods)
        missing_names = _period_names(missing_periods)
        rows: list[pd.Series] = []

        for period in window:
            period = pd.Period(period, freq="M")
            if period in account_group.index:
                row = account_group.loc[period].copy()
                row["Data Source"] = "Actual"
                row["Estimate Method"] = "Actual uploaded interval file"
                row["Confidence"] = "Normal"
            else:
                row, method, confidence = _estimate_row(account_group, period, actual_periods)
                row["Data Source"] = "Estimated"
                row["Estimate Method"] = method
                row["Confidence"] = confidence
                row["Peak During Non-Operating"] = False
                total = float(row.get("Total kWh", 0) or 0)
                non_operating = float(row.get("Non-Operating kWh", 0) or 0)
                row["Non-Operating %"] = 100 * non_operating / total if total else 0.0
                notes.append(
                    {
                        "Account number": account,
                        "Year": int(period.year),
                        "Uploaded months": uploaded_names,
                        "Missing months": missing_names,
                        "Estimated months": period.strftime("%B %Y"),
                        "Estimate method": method,
                        "Confidence level": confidence,
                        "Notes or warnings": "Reporting-month summary estimate only; no interval data was generated.",
                    }
                )

            row["Account"] = account
            row["Year"] = int(period.year)
            row["Month"] = int(period.month)
            rows.append(row)

        completed_groups.append(pd.DataFrame(rows))

    completed = pd.concat(completed_groups, ignore_index=True)
    completed = completed.sort_values(["Account", "Year", "Month"]).reset_index(drop=True)
    return completed, pd.DataFrame(notes, columns=NOTE_COLUMNS)
