"""Estimate missing reporting-month summaries without fabricating interval data."""

from __future__ import annotations

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


def detect_missing_months(summary: pd.DataFrame) -> dict[str, list[pd.Period]]:
    result: dict[str, list[pd.Period]] = {}
    if summary.empty:
        return result
    for account, group in _with_period(summary).groupby("Account"):
        actual = set(group["Period"])
        window = pd.period_range(end=max(actual), periods=12, freq="M")
        result[str(account)] = [period for period in window if period not in actual]
    return result


def _period_names(periods: list[pd.Period]) -> str:
    return ", ".join(period.strftime("%B %Y") for period in periods)


def _interpolate_row(
    group: pd.DataFrame,
    target: pd.Period,
    actual_periods: list[pd.Period],
) -> tuple[pd.Series, str, str]:
    if len(actual_periods) == 1:
        source = actual_periods[0]
        return group.loc[source].copy(), "Single-month carry-forward, low confidence", "Low"

    before = [period for period in actual_periods if period < target]
    after = [period for period in actual_periods if period > target]
    if before and after:
        left_period = max(before)
        right_period = min(after)
        distance = right_period.ordinal - left_period.ordinal
        weight = (target.ordinal - left_period.ordinal) / distance
        row = group.loc[left_period].copy()
        for column in NUMERIC_ESTIMATE_COLUMNS:
            if column not in group.columns:
                continue
            left = pd.to_numeric(pd.Series([group.loc[left_period, column]]), errors="coerce").iloc[0]
            right = pd.to_numeric(pd.Series([group.loc[right_period, column]]), errors="coerce").iloc[0]
            row[column] = float(left + (right - left) * weight) if pd.notna(left) and pd.notna(right) else np.nan
        gap = distance - 1
        if gap == 1:
            method = f"Interpolated from {left_period.strftime('%B %Y')} and {right_period.strftime('%B %Y')}"
        else:
            method = f"Linear interpolation from {left_period.strftime('%B %Y')} to {right_period.strftime('%B %Y')}"
        return row, method, "Normal"

    nearest = min(actual_periods, key=lambda period: abs(period.ordinal - target.ordinal))
    method = f"Nearest available month ({nearest.strftime('%B %Y')}), low confidence"
    return group.loc[nearest].copy(), method, "Low"


def estimate_missing_months(summary: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return one rolling 12-month view per account and estimation notes."""
    if summary.empty:
        return summary.copy(), pd.DataFrame(columns=NOTE_COLUMNS)

    completed_groups: list[pd.DataFrame] = []
    notes: list[dict[str, object]] = []
    period_summary = _with_period(summary)

    for account, account_group in period_summary.groupby("Account", sort=True):
        account_group = account_group.sort_values("Period").drop_duplicates("Period", keep="last")
        window = pd.period_range(end=account_group["Period"].max(), periods=12, freq="M")
        account_group = account_group[account_group["Period"].isin(window)].set_index("Period")
        actual_periods = sorted(account_group.index.tolist())
        if not actual_periods:
            continue

        missing_periods = [period for period in window if period not in account_group.index]
        uploaded_names = _period_names(actual_periods)
        missing_names = _period_names(missing_periods)
        rows: list[pd.Series] = []

        for period in window:
            if period in account_group.index:
                row = account_group.loc[period].copy()
                row["Data Source"] = "Actual"
                row["Estimate Method"] = "Actual uploaded interval file"
                row["Confidence"] = "Normal"
            else:
                row, method, confidence = _interpolate_row(account_group, period, actual_periods)
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
