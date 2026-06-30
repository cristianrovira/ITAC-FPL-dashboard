"""Report-period detection and coverage helpers."""

from __future__ import annotations

from collections import defaultdict
from typing import Mapping, Sequence

import pandas as pd

from .extraction import ExtractedFile
from .validation import FileKey


def assigned_period(item: ExtractedFile) -> pd.Period | None:
    if item.year is None or item.month is None:
        return None
    return pd.Period(year=int(item.year), month=int(item.month), freq="M")


def item_coverage_ratio(
    item: ExtractedFile,
    interval_overrides: Mapping[FileKey, float] | None = None,
) -> float | None:
    """Estimate how much of the assigned month is covered by interval rows."""
    period = assigned_period(item)
    if period is None:
        return None
    key = (item.account, item.filename)
    interval = interval_overrides.get(key) if interval_overrides and key in interval_overrides else item.detected_interval_hours
    if interval is None or not item.row_count:
        return None
    observed_hours = float(item.row_count) * float(interval)
    expected_hours = float(period.days_in_month * 24)
    return min(observed_hours / expected_hours, 1.0) if expected_hours else None


def coverage_by_account_period(
    files: Sequence[ExtractedFile],
    interval_overrides: Mapping[FileKey, float] | None = None,
) -> dict[tuple[str, pd.Period], float]:
    coverage: defaultdict[tuple[str, pd.Period], float] = defaultdict(float)
    for item in files:
        if item.errors:
            continue
        period = assigned_period(item)
        if period is None:
            continue
        ratio = item_coverage_ratio(item, interval_overrides)
        if ratio is not None:
            coverage[(item.account, period)] += ratio
    return {key: min(value, 1.0) for key, value in coverage.items()}


def suggested_report_end(
    files: Sequence[ExtractedFile],
    interval_overrides: Mapping[FileKey, float] | None = None,
    complete_threshold: float = 0.85,
) -> pd.Period | None:
    """Suggest an end month, preferring the latest reasonably complete upload."""
    periods: set[pd.Period] = set()
    complete_periods: set[pd.Period] = set()
    coverage = coverage_by_account_period(files, interval_overrides)
    for item in files:
        if item.errors:
            continue
        period = assigned_period(item)
        if period is None:
            continue
        periods.add(period)
        ratio = coverage.get((item.account, period))
        if ratio is None or ratio >= complete_threshold:
            complete_periods.add(period)
    if complete_periods:
        return max(complete_periods)
    return max(periods) if periods else None


def report_window(end: pd.Period, months: int = 12) -> pd.PeriodIndex:
    return pd.period_range(end=end, periods=months, freq="M")


def format_window(window: Sequence[pd.Period]) -> str:
    if not window:
        return ""
    return f"{window[0].strftime('%B %Y')} through {window[-1].strftime('%B %Y')}"


def account_report_windows(
    files: Sequence[ExtractedFile],
    end_period: pd.Period | None = None,
    months: int = 12,
    interval_overrides: Mapping[FileKey, float] | None = None,
) -> dict[str, pd.PeriodIndex]:
    """Build one report window per account using the selected global end month."""
    accounts = sorted({item.account for item in files if not item.errors and assigned_period(item) is not None})
    if not accounts:
        return {}
    end = end_period or suggested_report_end(files, interval_overrides)
    if end is None:
        return {}
    return {account: report_window(end, months) for account in accounts}


def partial_period_warnings(
    files: Sequence[ExtractedFile],
    interval_overrides: Mapping[FileKey, float] | None = None,
    threshold: float = 0.85,
) -> list[str]:
    warnings: list[str] = []
    coverage = coverage_by_account_period(files, interval_overrides)
    for (account, period), ratio in sorted(coverage.items(), key=lambda item: (item[0][0], item[0][1])):
        if ratio < threshold:
            warnings.append(
                f"{account} / {period.strftime('%B %Y')} appears to cover only about {ratio:.0%} "
                "of a full reporting month; estimates and totals for that period may be low."
            )
    return warnings
