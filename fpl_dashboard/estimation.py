"""Estimate missing reporting-month summaries without fabricating interval data."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np
import pandas as pd

from .processing import DEMAND_COLUMNS, ENERGY_COLUMNS


NUMERIC_ESTIMATE_COLUMNS = ENERGY_COLUMNS + DEMAND_COLUMNS
PRIMARY_ESTIMATE_COLUMNS = ["Total kWh", *DEMAND_COLUMNS]
PARTIAL_COVERAGE_THRESHOLD = 85.0
PROFILE_SOURCE_LIMIT = 6
CROSS_BUCKET_COLUMNS = [
    "On-Peak Operating kWh",
    "Off-Peak Operating kWh",
    "On-Peak Non-Operating kWh",
    "Off-Peak Non-Operating kWh",
]
DERIVED_ENERGY_COLUMNS = [
    "Operating kWh",
    "Non-Operating kWh",
    "On-Peak kWh",
    "Off-Peak kWh",
    *CROSS_BUCKET_COLUMNS,
    "Weekend kWh",
    "Overnight kWh",
]
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


def _series_number(row: pd.Series, column: str, default: float | None = None) -> float | None:
    value = pd.to_numeric(pd.Series([row.get(column, default)]), errors="coerce").iloc[0]
    return float(value) if pd.notna(value) else default


def _set_numeric(row: pd.Series, column: str, value: float | None) -> None:
    row[column] = float(max(value, 0.0)) if value is not None and pd.notna(value) else np.nan


def _coverage(row: pd.Series) -> float:
    value = pd.to_numeric(pd.Series([row.get("Coverage %", 100.0)]), errors="coerce").iloc[0]
    return float(value) if pd.notna(value) else 100.0


def _is_partial(row: pd.Series) -> bool:
    return str(row.get("Coverage Status", "Complete")) == "Partial" or _coverage(row) < PARTIAL_COVERAGE_THRESHOLD


def _confidence_for_steps(steps: int) -> str:
    return "Very Low" if steps >= 3 else "Low"


def _confidence_rank(confidence: str) -> int:
    return {"Normal": 3, "Low": 2, "Very Low": 1}.get(str(confidence), 1)


def _lower_confidence(*values: str) -> str:
    return min(values, key=_confidence_rank)


def _column_average(group: pd.DataFrame, periods: Sequence[pd.Period], column: str) -> float | None:
    values = [_numeric_value(group, period, column) for period in periods if period in group.index and column in group.columns]
    clean = [value for value in values if value is not None and value > 0]
    return float(np.mean(clean)) if clean else None


def _guardrail(value: float | None, average: float | None, steps: int) -> float | None:
    if value is None or pd.isna(value):
        return average
    if average is None or average <= 0:
        return value
    lower = average * (0.55 if steps <= 3 else 0.45)
    upper = average * (1.65 if steps <= 3 else 1.90)
    if value <= 0:
        return lower
    return min(max(value, lower), upper)


def _usable_profile_periods(group: pd.DataFrame, target: pd.Period, anchor_periods: Sequence[pd.Period]) -> list[pd.Period]:
    complete = []
    fallback = []
    for period in anchor_periods:
        if period not in group.index:
            continue
        row = group.loc[period]
        total = _series_number(row, "Total kWh", 0.0) or 0.0
        if total <= 0:
            continue
        fallback.append(period)
        if not _is_partial(row):
            complete.append(period)
    periods = complete or fallback
    return sorted(periods, key=lambda period: abs(period.ordinal - target.ordinal))[:PROFILE_SOURCE_LIMIT]


def _sum_column(group: pd.DataFrame, periods: Sequence[pd.Period], column: str) -> float:
    if column not in group.columns:
        return 0.0
    total = 0.0
    for period in periods:
        if period in group.index:
            total += _numeric_value(group, period, column) or 0.0
    return float(total)


def _safe_ratio(numerator: float, denominator: float) -> float:
    return float(numerator / denominator) if denominator else 0.0


def _ratio_note(periods: Sequence[pd.Period]) -> str:
    if not periods:
        return "No reliable category-ratio months were available."
    return "Category ratios from " + _period_names(periods) + "."


def _apply_energy_profile(row: pd.Series, group: pd.DataFrame, target: pd.Period, anchor_periods: Sequence[pd.Period]) -> str:
    """Allocate estimated total kWh into category buckets using reliable actual-month ratios."""
    total = _series_number(row, "Total kWh", 0.0) or 0.0
    if total <= 0:
        for column in DERIVED_ENERGY_COLUMNS:
            if column in row:
                row[column] = 0.0
        row["Non-Operating %"] = 0.0
        return "No positive total kWh was available for category allocation."

    profile_periods = _usable_profile_periods(group, target, anchor_periods)
    profile_total = _sum_column(group, profile_periods, "Total kWh")

    cross_sum = sum(_sum_column(group, profile_periods, column) for column in CROSS_BUCKET_COLUMNS)
    if cross_sum > 0:
        cross_values = {
            column: total * _safe_ratio(_sum_column(group, profile_periods, column), cross_sum)
            for column in CROSS_BUCKET_COLUMNS
        }
        for column, value in cross_values.items():
            if column in row:
                row[column] = value
        row["Operating kWh"] = cross_values["On-Peak Operating kWh"] + cross_values["Off-Peak Operating kWh"]
        row["Non-Operating kWh"] = cross_values["On-Peak Non-Operating kWh"] + cross_values["Off-Peak Non-Operating kWh"]
        row["On-Peak kWh"] = cross_values["On-Peak Operating kWh"] + cross_values["On-Peak Non-Operating kWh"]
        row["Off-Peak kWh"] = cross_values["Off-Peak Operating kWh"] + cross_values["Off-Peak Non-Operating kWh"]
    elif profile_total > 0:
        operating = total * _safe_ratio(_sum_column(group, profile_periods, "Operating kWh"), profile_total)
        on_peak = total * _safe_ratio(_sum_column(group, profile_periods, "On-Peak kWh"), profile_total)
        row["Operating kWh"] = operating
        row["Non-Operating kWh"] = max(total - operating, 0.0)
        row["On-Peak kWh"] = on_peak
        row["Off-Peak kWh"] = max(total - on_peak, 0.0)
    else:
        existing_non_operating = _series_number(row, "Non-Operating kWh", 0.0) or 0.0
        row["Non-Operating kWh"] = min(existing_non_operating, total)
        row["Operating kWh"] = max(total - row["Non-Operating kWh"], 0.0)

    for column in ["Weekend kWh", "Overnight kWh"]:
        if column in row and profile_total > 0:
            row[column] = total * _safe_ratio(_sum_column(group, profile_periods, column), profile_total)

    non_operating = _series_number(row, "Non-Operating kWh", 0.0) or 0.0
    row["Non-Operating %"] = 100 * non_operating / total if total else 0.0
    return _ratio_note(profile_periods)


def _quality_score(data_source: str, coverage_status: str, confidence: str, coverage: float | None) -> tuple[int, str]:
    if data_source == "Actual":
        if coverage is not None and coverage < 99:
            return 90, "High"
        return 100, "High"
    if coverage_status == "Partial Estimated":
        score = int(max(15, min(70, round((coverage or 0) * 0.9))))
        return score, "Low" if score >= 35 else "Very Low"
    if confidence == "Normal":
        return 70, "Medium"
    if confidence == "Low":
        return 50, "Low"
    return 25, "Very Low"


def _apply_quality(row: pd.Series, data_source: str, coverage_status: str, confidence: str, method: str) -> None:
    coverage_value = _series_number(row, "Coverage %", None)
    score, level = _quality_score(data_source, coverage_status, confidence, coverage_value)
    row["Quality Score"] = score
    row["Quality Level"] = level
    if data_source == "Actual":
        coverage_text = f"{coverage_value:.0f}%" if coverage_value is not None else "complete"
        row["Quality Notes"] = f"Actual uploaded interval file with {coverage_text} coverage."
    elif coverage_status == "Partial Estimated":
        coverage_text = f"{coverage_value:.0f}%" if coverage_value is not None else "partial"
        row["Quality Notes"] = f"Partial upload ({coverage_text} coverage) blended with complete-month trend and category ratios."
    else:
        row["Quality Notes"] = "Missing reporting month estimated from complete uploaded months; no interval rows were fabricated."


def _single_month_row(group: pd.DataFrame, target: pd.Period, anchor_periods: list[pd.Period]) -> tuple[pd.Series, str, str]:
    source = anchor_periods[0]
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
    for column in PRIMARY_ESTIMATE_COLUMNS:
        if column not in group.columns:
            continue
        left = _numeric_value(group, left_period, column)
        right = _numeric_value(group, right_period, column)
        _set_numeric(row, column, left + (right - left) * weight if left is not None and right is not None else None)
    gap = distance - 1
    confidence = "Normal" if gap <= 2 else "Low"
    if gap == 1:
        method = f"Interpolated total kWh from {left_period.strftime('%B %Y')} and {right_period.strftime('%B %Y')}"
    else:
        method = f"Linear total-kWh interpolation from {left_period.strftime('%B %Y')} to {right_period.strftime('%B %Y')}"
    return row, method, confidence


def _trend_extrapolated_row(
    group: pd.DataFrame,
    target: pd.Period,
    anchor_periods: list[pd.Period],
) -> tuple[pd.Series, str, str]:
    if len(anchor_periods) == 1:
        return _single_month_row(group, target, anchor_periods)

    if target < anchor_periods[0]:
        anchor = anchor_periods[0]
        neighbor = anchor_periods[1]
        steps = anchor.ordinal - target.ordinal
        direction = "backward"
    else:
        anchor = anchor_periods[-1]
        neighbor = anchor_periods[-2]
        steps = target.ordinal - anchor.ordinal
        direction = "forward"

    distance = abs(anchor.ordinal - neighbor.ordinal) or 1
    row = group.loc[anchor].copy()
    for column in PRIMARY_ESTIMATE_COLUMNS:
        if column not in group.columns:
            continue
        anchor_value = _numeric_value(group, anchor, column)
        neighbor_value = _numeric_value(group, neighbor, column)
        if anchor_value is None or neighbor_value is None:
            estimate = anchor_value
        else:
            monthly_change = (anchor_value - neighbor_value) / distance
            estimate = anchor_value + monthly_change * steps
            if anchor_value > 0:
                estimate = min(estimate, anchor_value * (1 + 0.35 * steps))
        average = _column_average(group, anchor_periods[: min(len(anchor_periods), 5)], column)
        _set_numeric(row, column, _guardrail(estimate, average, steps))

    confidence = _confidence_for_steps(steps)
    method = (
        f"Trend extrapolated total kWh {direction} from {anchor.strftime('%B %Y')} "
        f"and {neighbor.strftime('%B %Y')}"
    )
    return row, method, confidence


def _estimate_row(
    group: pd.DataFrame,
    target: pd.Period,
    anchor_periods: list[pd.Period],
) -> tuple[pd.Series, str, str]:
    if len(anchor_periods) == 1:
        return _single_month_row(group, target, anchor_periods)

    before = [period for period in anchor_periods if period < target]
    after = [period for period in anchor_periods if period > target]
    if before and after:
        return _linear_interpolated_row(group, target, max(before), min(after))

    return _trend_extrapolated_row(group, target, anchor_periods)


def _partial_scaled_row(
    group: pd.DataFrame,
    target: pd.Period,
    anchor_periods: list[pd.Period],
) -> tuple[pd.Series, str, str]:
    source = group.loc[target].copy()
    coverage = max(_coverage(source), 1.0)
    factor = min(100.0 / coverage, 12.0)
    uploaded_total = _series_number(source, "Total kWh", 0.0) or 0.0
    scaled_total = uploaded_total * factor

    trend_row, trend_method, trend_confidence = _estimate_row(group, target, anchor_periods)
    trend_total = _series_number(trend_row, "Total kWh", None)
    coverage_weight = min(max(coverage / 100.0, 0.10), 0.65)
    if trend_total is not None and trend_total > 0:
        blended_total = scaled_total * coverage_weight + trend_total * (1 - coverage_weight)
        nearest_steps = min(abs(period.ordinal - target.ordinal) for period in anchor_periods) if anchor_periods else 1
        anchor_average = _column_average(group, anchor_periods[: min(len(anchor_periods), 5)], "Total kWh")
        total_estimate = _guardrail(blended_total, anchor_average, nearest_steps)
    else:
        total_estimate = scaled_total

    row = source.copy()
    _set_numeric(row, "Total kWh", total_estimate)
    for column in DEMAND_COLUMNS:
        if column not in row:
            continue
        value = _series_number(row, column, None)
        if value is None or value <= 0:
            _set_numeric(row, column, _column_average(group, anchor_periods, column))

    profile_note = _apply_energy_profile(row, group, target, anchor_periods)
    row["Coverage %"] = coverage
    row["Coverage Status"] = "Partial Estimated"
    confidence = _lower_confidence("Very Low" if coverage < 25 else "Low", trend_confidence)
    method = (
        f"Partial-month blended estimate from {coverage:.0f}% uploaded coverage and complete-month trend; "
        f"excluded from trend anchors. {trend_method}. {profile_note}"
    )
    return row, method, confidence


def _fallback_anchor_periods(group: pd.DataFrame, actual_periods: list[pd.Period]) -> list[pd.Period]:
    complete = []
    for period in actual_periods:
        row = group.loc[period]
        total = float(pd.to_numeric(pd.Series([row.get("Total kWh", 0)]), errors="coerce").fillna(0).iloc[0])
        if not _is_partial(row) and total > 0:
            complete.append(period)
    if complete:
        return complete
    nonzero = []
    for period in actual_periods:
        row = group.loc[period]
        total = float(pd.to_numeric(pd.Series([row.get("Total kWh", 0)]), errors="coerce").fillna(0).iloc[0])
        if total > 0:
            nonzero.append(period)
    return nonzero or actual_periods


def _note(
    account: object,
    period: pd.Period,
    actual_periods: Sequence[pd.Period],
    affected_periods: Sequence[pd.Period],
    method: str,
    confidence: str,
) -> dict[str, object]:
    return {
        "Account number": account,
        "Year": int(period.year),
        "Uploaded months": _period_names(actual_periods),
        "Missing months": _period_names(affected_periods),
        "Estimated months": period.strftime("%B %Y"),
        "Estimate method": method,
        "Confidence level": confidence,
        "Notes or warnings": "Monthly summary estimate only; no interval data was generated.",
    }


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

        anchor_periods = _fallback_anchor_periods(account_group, actual_periods)
        window = explicit_windows.get(str(account), pd.period_range(end=max(actual_periods), periods=12, freq="M"))
        affected_periods = [
            pd.Period(period, freq="M")
            for period in window
            if pd.Period(period, freq="M") not in account_group.index or _is_partial(account_group.loc[pd.Period(period, freq="M")])
        ]
        rows: list[pd.Series] = []

        for period in window:
            period = pd.Period(period, freq="M")
            if period in account_group.index and not _is_partial(account_group.loc[period]):
                row = account_group.loc[period].copy()
                row["Data Source"] = "Actual"
                row["Estimate Method"] = "Actual uploaded interval file"
                row["Confidence"] = "Normal"
                row["Coverage Status"] = row.get("Coverage Status", "Complete")
                _apply_quality(row, "Actual", str(row["Coverage Status"]), "Normal", str(row["Estimate Method"]))
            elif period in account_group.index and _is_partial(account_group.loc[period]):
                row, method, confidence = _partial_scaled_row(account_group, period, anchor_periods)
                row["Data Source"] = "Estimated"
                row["Estimate Method"] = method
                row["Confidence"] = confidence
                row["Peak During Non-Operating"] = False
                _apply_quality(row, "Estimated", "Partial Estimated", confidence, method)
                notes.append(_note(account, period, actual_periods, affected_periods, method, confidence))
            else:
                row, method, confidence = _estimate_row(account_group, period, anchor_periods)
                profile_note = _apply_energy_profile(row, account_group, period, anchor_periods)
                method = f"{method}. {profile_note}"
                row["Data Source"] = "Estimated"
                row["Estimate Method"] = method
                row["Confidence"] = confidence
                row["Peak During Non-Operating"] = False
                row["Coverage %"] = np.nan
                row["Coverage Status"] = "Estimated Missing"
                row["Uploaded Row Count"] = 0
                if "Expected Row Count" not in row or pd.isna(row.get("Expected Row Count")):
                    row["Expected Row Count"] = np.nan
                _apply_quality(row, "Estimated", "Estimated Missing", confidence, method)
                notes.append(_note(account, period, actual_periods, affected_periods, method, confidence))

            row["Account"] = account
            row["Year"] = int(period.year)
            row["Month"] = int(period.month)
            rows.append(row)

        completed_groups.append(pd.DataFrame(rows))

    completed = pd.concat(completed_groups, ignore_index=True)
    completed = completed.sort_values(["Account", "Year", "Month"]).reset_index(drop=True)
    return completed, pd.DataFrame(notes, columns=NOTE_COLUMNS)
