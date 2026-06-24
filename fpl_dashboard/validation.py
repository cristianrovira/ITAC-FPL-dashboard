"""Validation helpers and the pre-processing file log."""

from __future__ import annotations

from collections import defaultdict
from typing import Mapping, Sequence

import pandas as pd

from .extraction import ExtractedFile
from .utils import MONTH_NAMES, interval_label, join_messages


FileKey = tuple[str, str]


def selected_demand_columns(
    item: ExtractedFile,
    selections: Mapping[FileKey, Sequence[str]] | None = None,
) -> list[str]:
    if selections and (item.account, item.filename) in selections:
        return list(selections[(item.account, item.filename)])
    return list(item.demand_columns)


def _assigned_period(item: ExtractedFile) -> pd.Period | None:
    if item.year is None or item.month is None:
        return None
    return pd.Period(year=int(item.year), month=int(item.month), freq="M")


def rolling_analysis_windows(files: Sequence[ExtractedFile]) -> dict[str, pd.PeriodIndex]:
    """Build one 12-period window per account, ending at its latest assigned month."""
    periods_by_account: defaultdict[str, set[pd.Period]] = defaultdict(set)
    for item in files:
        if item.errors:
            continue
        period = _assigned_period(item)
        if period is not None:
            periods_by_account[item.account].add(period)
    return {
        account: pd.period_range(end=max(periods), periods=12, freq="M")
        for account, periods in periods_by_account.items()
        if periods
    }


def validate_files(
    files: Sequence[ExtractedFile],
    demand_selections: Mapping[FileKey, Sequence[str]] | None = None,
    interval_overrides: Mapping[FileKey, float] | None = None,
) -> tuple[pd.DataFrame, list[str], list[str]]:
    """Return a user-facing file log plus collection errors and warnings."""
    rows: list[dict[str, object]] = []
    collection_errors: list[str] = []
    collection_warnings: list[str] = []
    period_files: defaultdict[tuple[str, pd.Period], list[str]] = defaultdict(list)
    intervals_by_account: defaultdict[str, set[float]] = defaultdict(set)

    for item in files:
        errors = list(item.errors)
        warnings = list(item.warnings)
        demand_columns = selected_demand_columns(item, demand_selections)
        if not demand_columns:
            errors.append("No demand column is selected.")
        elif item.dataframe is not None:
            missing = [column for column in demand_columns if column not in item.dataframe.columns]
            if missing:
                errors.append(f"Selected demand columns not found: {', '.join(missing)}")

        key = (item.account, item.filename)
        interval = interval_overrides.get(key) if interval_overrides and key in interval_overrides else item.detected_interval_hours
        if interval is None:
            errors.append("The data interval was not detected or selected.")
        else:
            intervals_by_account[item.account].add(float(interval))

        assigned_period = _assigned_period(item)
        if assigned_period is not None:
            period_files[(item.account, assigned_period)].append(item.filename)

        status = "Error" if errors else ("Warning" if warnings else "Valid")
        rows.append(
            {
                "Account": item.account,
                "File name": item.filename,
                "Assigned reporting month": MONTH_NAMES[item.month - 1] if item.month else "",
                "Assigned reporting year": item.year or "",
                "Timestamp column": item.timestamp_column or "",
                "Demand column(s)": ", ".join(demand_columns),
                "Detected interval": interval_label(interval),
                "Row count": item.row_count,
                "Status": status,
                "Warning or error message": join_messages(errors + warnings),
            }
        )

    for (account, period), names in period_files.items():
        unique_names = list(dict.fromkeys(names))
        if len(unique_names) > 1:
            collection_errors.append(
                f"Duplicate reporting month for {account}: {period.strftime('%B %Y')} appears in "
                + ", ".join(unique_names)
                + "."
            )

    for account, intervals in intervals_by_account.items():
        if len(intervals) > 1:
            collection_warnings.append(
                f"{account} contains inconsistent detected intervals: "
                + ", ".join(interval_label(value) for value in sorted(intervals))
                + "."
            )

    for row in rows:
        if row["Status"] == "Error":
            collection_errors.append(f"{row['Account']} / {row['File name']}: {row['Warning or error message']}")

    return pd.DataFrame(rows), list(dict.fromkeys(collection_errors)), collection_warnings


def missing_months_by_account(files: Sequence[ExtractedFile]) -> dict[str, list[pd.Period]]:
    present: defaultdict[str, set[pd.Period]] = defaultdict(set)
    for item in files:
        if item.errors:
            continue
        period = _assigned_period(item)
        if period is not None:
            present[item.account].add(period)

    windows = rolling_analysis_windows(files)
    return {
        account: [period for period in window if period not in present[account]]
        for account, window in windows.items()
    }
