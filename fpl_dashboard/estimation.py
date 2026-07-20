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
SEASONAL_SOURCE_LIMIT = 6
DEFAULT_SEASONAL_WEIGHT = 0.65
DEFAULT_LOCAL_WEIGHT = 0.35
SAME_MONTH_SEASONAL_WEIGHT = 0.80
TWO_SIDED_LOCAL_WEIGHT = 0.65
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
    "Account",
    "Estimated month",
    "Year",
    "Uploaded months",
    "Missing months",
    "Seasonal reference months",
    "Local reference months",
    "Same-month reference",
    "Seasonal estimate",
    "Local estimate",
    "Final blended estimate",
    "Estimate Method",
    "Confidence",
    "Coverage percentage",
    "Quality Notes",
    "Candidate Diagnostics",
    # Legacy column names kept for older downstream spreadsheets/tests.
    "Account number",
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


def circular_month_distance(a: int, b: int) -> int:
    """Return seasonal month distance on a 12-month circular calendar."""
    difference = abs(int(a) - int(b))
    return min(difference, 12 - difference)


def _window_positions(window: Sequence[pd.Period]) -> dict[pd.Period, int]:
    return {pd.Period(period, freq="M"): index for index, period in enumerate(window)}


def _circular_position_distance(a: int, b: int, length: int) -> int:
    difference = abs(int(a) - int(b))
    return min(difference, int(length) - difference)


def _seasonal_weight(target: pd.Period, anchor: pd.Period) -> float:
    distance = circular_month_distance(target.month, anchor.month)
    if distance == 0:
        # Same calendar month from another year is the strongest reusable seasonal signal.
        return 8.0
    # Inverse-square weighting keeps adjacent months important without allowing distant months to dominate.
    return 1.0 / float(distance * distance)


def _complete_anchor_periods(group: pd.DataFrame, actual_periods: Sequence[pd.Period]) -> list[pd.Period]:
    anchors: list[pd.Period] = []
    for period in actual_periods:
        if period not in group.index:
            continue
        row = group.loc[period]
        total = _series_number(row, "Total kWh", 0.0) or 0.0
        if total > 0 and not _is_partial(row):
            anchors.append(period)
    return anchors


def _seasonal_anchor_periods(group: pd.DataFrame, target: pd.Period, anchor_periods: Sequence[pd.Period], limit: int = SEASONAL_SOURCE_LIMIT) -> list[pd.Period]:
    usable = [period for period in anchor_periods if period in group.index and (_numeric_value(group, period, "Total kWh") or 0.0) > 0]
    return sorted(
        usable,
        key=lambda period: (
            circular_month_distance(target.month, period.month),
            abs(period.ordinal - target.ordinal),
        ),
    )[:limit]


def _weighted_column_estimate(group: pd.DataFrame, target: pd.Period, periods: Sequence[pd.Period], column: str) -> float | None:
    weighted_total = 0.0
    total_weight = 0.0
    for period in periods:
        value = _numeric_value(group, period, column)
        if value is None:
            continue
        weight = _seasonal_weight(target, period)
        weighted_total += value * weight
        total_weight += weight
    return weighted_total / total_weight if total_weight else None


def _seasonal_estimate_row(group: pd.DataFrame, target: pd.Period, anchor_periods: Sequence[pd.Period]) -> tuple[pd.Series, float | None, list[pd.Period], str]:
    seasonal_periods = _seasonal_anchor_periods(group, target, anchor_periods)
    if not seasonal_periods:
        row = group.loc[anchor_periods[0]].copy() if anchor_periods else pd.Series(dtype=object)
        return row, None, [], "No complete seasonal anchors available"
    row = group.loc[seasonal_periods[0]].copy()
    for column in PRIMARY_ESTIMATE_COLUMNS:
        if column in group.columns:
            _set_numeric(row, column, _weighted_column_estimate(group, target, seasonal_periods, column))
    total = _series_number(row, "Total kWh", None)
    diagnostics = ", ".join(
        f"{period.strftime('%B %Y')} d={circular_month_distance(target.month, period.month)} w={_seasonal_weight(target, period):.2f}"
        for period in seasonal_periods
    )
    return row, total, seasonal_periods, diagnostics


def _chronological_neighbors(anchor_periods: Sequence[pd.Period], target: pd.Period) -> tuple[pd.Period | None, pd.Period | None]:
    before = [period for period in anchor_periods if period < target]
    after = [period for period in anchor_periods if period > target]
    return (max(before) if before else None, min(after) if after else None)


def _circular_neighbors(anchor_periods: Sequence[pd.Period], target: pd.Period, window: Sequence[pd.Period]) -> tuple[pd.Period | None, pd.Period | None]:
    positions = _window_positions(window)
    target = pd.Period(target, freq="M")
    if target not in positions or len(window) < 2:
        return None, None
    target_pos = positions[target]
    length = len(window)
    in_window_anchors = [pd.Period(period, freq="M") for period in anchor_periods if pd.Period(period, freq="M") in positions and pd.Period(period, freq="M") != target]
    previous = sorted(
        in_window_anchors,
        key=lambda period: ((target_pos - positions[period]) % length or length, abs(period.ordinal - target.ordinal)),
    )
    following = sorted(
        in_window_anchors,
        key=lambda period: ((positions[period] - target_pos) % length or length, abs(period.ordinal - target.ordinal)),
    )
    left = previous[0] if previous else None
    right = following[0] if following else None
    if left == right:
        return None, None
    return left, right


def _linear_values(group: pd.DataFrame, target: pd.Period, left_period: pd.Period, right_period: pd.Period) -> tuple[pd.Series, float | None]:
    distance = right_period.ordinal - left_period.ordinal
    if distance <= 0:
        return group.loc[left_period].copy(), _numeric_value(group, left_period, "Total kWh")
    weight = (target.ordinal - left_period.ordinal) / distance
    row = group.loc[left_period].copy()
    for column in PRIMARY_ESTIMATE_COLUMNS:
        if column not in group.columns:
            continue
        left = _numeric_value(group, left_period, column)
        right = _numeric_value(group, right_period, column)
        _set_numeric(row, column, left + (right - left) * weight if left is not None and right is not None else None)
    return row, _series_number(row, "Total kWh", None)


def _circular_interpolated_values(
    group: pd.DataFrame,
    target: pd.Period,
    left_period: pd.Period,
    right_period: pd.Period,
    window: Sequence[pd.Period],
) -> tuple[pd.Series, float | None]:
    positions = _window_positions(window)
    length = len(window)
    left_pos = positions[left_period]
    right_pos = positions[right_period]
    target_pos = positions[target]
    distance = (right_pos - left_pos) % length
    if distance == 0:
        return group.loc[left_period].copy(), _numeric_value(group, left_period, "Total kWh")
    weight = ((target_pos - left_pos) % length) / distance
    row = group.loc[left_period].copy()
    for column in PRIMARY_ESTIMATE_COLUMNS:
        if column not in group.columns:
            continue
        left = _numeric_value(group, left_period, column)
        right = _numeric_value(group, right_period, column)
        _set_numeric(row, column, left + (right - left) * weight if left is not None and right is not None else None)
    return row, _series_number(row, "Total kWh", None)


def _blend_values(seasonal: float | None, local: float | None, seasonal_weight: float) -> float | None:
    if seasonal is None:
        return local
    if local is None:
        return seasonal
    return seasonal * seasonal_weight + local * (1 - seasonal_weight)


def _blend_rows(
    seasonal_row: pd.Series,
    local_row: pd.Series | None,
    seasonal_weight: float,
    group: pd.DataFrame,
    anchor_periods: Sequence[pd.Period],
    steps: int,
) -> pd.Series:
    if local_row is None:
        return seasonal_row.copy()
    row = seasonal_row.copy()
    for column in PRIMARY_ESTIMATE_COLUMNS:
        if column not in row:
            continue
        seasonal_value = _series_number(seasonal_row, column, None)
        local_value = _series_number(local_row, column, None)
        blended = _blend_values(seasonal_value, local_value, seasonal_weight)
        average = _column_average(group, anchor_periods[: min(len(anchor_periods), 5)], column)
        _set_numeric(row, column, _guardrail(blended, average, max(steps, 1)))
    return row


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
    return {
        "Normal": 5,
        "High Estimate Confidence": 4,
        "Medium Estimate Confidence": 3,
        "Low": 2,
        "Very Low": 1,
    }.get(str(confidence), 1)


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
    return sorted(
        periods,
        key=lambda period: (
            circular_month_distance(target.month, period.month),
            abs(period.ordinal - target.ordinal),
        ),
    )[:PROFILE_SOURCE_LIMIT]


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


def _reconcile_energy_categories(row: pd.Series) -> None:
    total = _series_number(row, "Total kWh", 0.0) or 0.0
    op_on = _series_number(row, "On-Peak Operating kWh", 0.0) or 0.0
    op_off = _series_number(row, "Off-Peak Operating kWh", 0.0) or 0.0
    nop_on = _series_number(row, "On-Peak Non-Operating kWh", 0.0) or 0.0
    nop_off = _series_number(row, "Off-Peak Non-Operating kWh", 0.0) or 0.0
    cross_total = op_on + op_off + nop_on + nop_off
    if cross_total > 0:
        if abs(cross_total - total) > 1e-9:
            scale = total / cross_total
            op_on *= scale
            op_off *= scale
            nop_on *= scale
            nop_off *= scale
        row["On-Peak Operating kWh"] = op_on
        row["Off-Peak Operating kWh"] = op_off
        row["On-Peak Non-Operating kWh"] = nop_on
        row["Off-Peak Non-Operating kWh"] = nop_off
        row["Operating kWh"] = op_on + op_off
        row["Non-Operating kWh"] = nop_on + nop_off
        row["On-Peak kWh"] = op_on + nop_on
        row["Off-Peak kWh"] = op_off + nop_off
    else:
        operating = min(_series_number(row, "Operating kWh", total) or 0.0, total)
        row["Operating kWh"] = operating
        row["Non-Operating kWh"] = max(total - operating, 0.0)
        on_peak = min(_series_number(row, "On-Peak kWh", 0.0) or 0.0, total)
        row["On-Peak kWh"] = on_peak
        row["Off-Peak kWh"] = max(total - on_peak, 0.0)
    row["Non-Operating %"] = 100 * row["Non-Operating kWh"] / total if total else 0.0


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

    _reconcile_energy_categories(row)
    return _ratio_note(profile_periods)


def _quality_score(data_source: str, coverage_status: str, confidence: str, coverage: float | None) -> tuple[int, str]:
    if data_source == "Actual":
        if coverage is not None and coverage < 99:
            return 90, "High"
        return 100, "High"
    if coverage_status == "Partial Estimated":
        score = int(max(15, min(70, round((coverage or 0) * 0.9))))
        return score, "Low" if score >= 35 else "Very Low"
    if confidence == "High Estimate Confidence":
        return 80, "High"
    if confidence == "Medium Estimate Confidence":
        return 65, "Medium"
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
        row["Quality Notes"] = f"Partial upload ({coverage_text} coverage) blended with seasonal/monthly estimate and category ratios."
    else:
        row["Quality Notes"] = "Missing reporting month estimated from seasonal month-of-year patterns and nearby actual months; no interval rows were fabricated."


def _single_month_row(group: pd.DataFrame, target: pd.Period, anchor_periods: list[pd.Period]) -> tuple[pd.Series, str, str, dict[str, object]]:
    source = anchor_periods[0]
    confidence = "Very Low" if circular_month_distance(target.month, source.month) > 2 else "Low"
    diagnostics = {
        "seasonal_refs": [source],
        "local_refs": [source],
        "same_month_ref": "",
        "seasonal_estimate": _numeric_value(group, source, "Total kWh"),
        "local_estimate": _numeric_value(group, source, "Total kWh"),
        "final_estimate": _numeric_value(group, source, "Total kWh"),
        "candidate_diagnostics": f"single anchor {source.strftime('%B %Y')} distance={circular_month_distance(target.month, source.month)}",
    }
    return group.loc[source].copy(), f"Single-month carry-forward from {source.strftime('%B %Y')}", confidence, diagnostics


def _linear_interpolated_row(
    group: pd.DataFrame,
    target: pd.Period,
    left_period: pd.Period,
    right_period: pd.Period,
) -> tuple[pd.Series, str, str, dict[str, object]]:
    row, local_total = _linear_values(group, target, left_period, right_period)
    gap = right_period.ordinal - left_period.ordinal - 1
    confidence = "Medium Estimate Confidence" if gap <= 3 else "Low"
    if gap == 1:
        method = f"Linear interpolation between {left_period.strftime('%B %Y')} and {right_period.strftime('%B %Y')}"
    else:
        method = f"Linear interpolation from {left_period.strftime('%B %Y')} to {right_period.strftime('%B %Y')}"
    diagnostics = {
        "seasonal_refs": [],
        "local_refs": [left_period, right_period],
        "same_month_ref": "",
        "seasonal_estimate": np.nan,
        "local_estimate": local_total,
        "final_estimate": local_total,
        "candidate_diagnostics": f"chronological gap={gap}; local={local_total}",
    }
    return row, method, confidence, diagnostics


def _trend_local_row(
    group: pd.DataFrame,
    target: pd.Period,
    anchor_periods: list[pd.Period],
) -> tuple[pd.Series | None, float | None, list[pd.Period], str, int, str]:
    if len(anchor_periods) == 1:
        source = anchor_periods[0]
        return group.loc[source].copy(), _numeric_value(group, source, "Total kWh"), [source], "carry-forward", abs(source.ordinal - target.ordinal)

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
    return row, _series_number(row, "Total kWh", None), [anchor, neighbor], direction, steps


def _seasonal_hybrid_row(
    group: pd.DataFrame,
    target: pd.Period,
    anchor_periods: list[pd.Period],
    window: Sequence[pd.Period],
) -> tuple[pd.Series, str, str, dict[str, object]]:
    if len(anchor_periods) == 1:
        return _single_month_row(group, target, anchor_periods)

    seasonal_row, seasonal_total, seasonal_refs, candidate_diagnostics = _seasonal_estimate_row(group, target, anchor_periods)
    same_month_refs = [period for period in seasonal_refs if period.month == target.month and period != target]

    if same_month_refs:
        local_row, local_total, local_refs, direction, steps = _trend_local_row(group, target, anchor_periods)
        row = _blend_rows(seasonal_row, local_row, SAME_MONTH_SEASONAL_WEIGHT if local_row is not None else 1.0, group, anchor_periods, steps)
        final_total = _series_number(row, "Total kWh", None)
        method = f"Same-month reference from {same_month_refs[0].strftime('%B %Y')} with local trend adjustment"
        confidence = "High Estimate Confidence" if len(seasonal_refs) >= 2 else "Medium Estimate Confidence"
        diagnostics = {
            "seasonal_refs": seasonal_refs,
            "local_refs": local_refs,
            "same_month_ref": _period_names(same_month_refs),
            "seasonal_estimate": seasonal_total,
            "local_estimate": local_total,
            "final_estimate": final_total,
            "candidate_diagnostics": candidate_diagnostics + f"; local {direction} refs={_period_names(local_refs)}",
        }
        return row, method, confidence, diagnostics

    before, after = _chronological_neighbors(anchor_periods, target)
    if before is not None and after is not None:
        return _linear_interpolated_row(group, target, before, after)

    circular_left, circular_right = _circular_neighbors(anchor_periods, target, window)
    if circular_left is not None and circular_right is not None:
        local_row, local_total = _circular_interpolated_values(group, target, circular_left, circular_right, window)
        steps = _circular_position_distance(_window_positions(window)[circular_left], _window_positions(window)[circular_right], len(window))
        row = _blend_rows(seasonal_row, local_row, DEFAULT_SEASONAL_WEIGHT, group, anchor_periods, steps)
        final_total = _series_number(row, "Total kWh", None)
        confidence = "Medium Estimate Confidence" if steps <= 4 and len(seasonal_refs) >= 2 else "Low"
        method = f"Circular interpolation between {circular_left.strftime('%B %Y')} and {circular_right.strftime('%B %Y')} with seasonal wraparound support"
        diagnostics = {
            "seasonal_refs": seasonal_refs,
            "local_refs": [circular_left, circular_right],
            "same_month_ref": "",
            "seasonal_estimate": seasonal_total,
            "local_estimate": local_total,
            "final_estimate": final_total,
            "candidate_diagnostics": candidate_diagnostics + f"; circular refs={_period_names([circular_left, circular_right])}",
        }
        return row, method, confidence, diagnostics

    local_row, local_total, local_refs, direction, steps = _trend_local_row(group, target, anchor_periods)
    row = _blend_rows(seasonal_row, local_row, DEFAULT_SEASONAL_WEIGHT if seasonal_total is not None else 0.0, group, anchor_periods, steps)
    final_total = _series_number(row, "Total kWh", None)
    if seasonal_total is None:
        confidence = _confidence_for_steps(steps)
        method = f"Conservative {direction} extrapolation from {_period_names(local_refs)}"
    else:
        confidence = "Low" if steps <= 3 and len(seasonal_refs) >= 2 else "Very Low"
        method = f"Hybrid seasonal/local estimate using {_period_names(seasonal_refs[:3])} and {_period_names(local_refs)}"
    diagnostics = {
        "seasonal_refs": seasonal_refs,
        "local_refs": local_refs,
        "same_month_ref": "",
        "seasonal_estimate": seasonal_total,
        "local_estimate": local_total,
        "final_estimate": final_total,
        "candidate_diagnostics": candidate_diagnostics + f"; local {direction} steps={steps} refs={_period_names(local_refs)}",
    }
    return row, method, confidence, diagnostics


def _estimate_row(
    group: pd.DataFrame,
    target: pd.Period,
    anchor_periods: list[pd.Period],
    window: Sequence[pd.Period],
) -> tuple[pd.Series, str, str, dict[str, object]]:
    return _seasonal_hybrid_row(group, target, anchor_periods, window)


def _partial_scaled_row(
    group: pd.DataFrame,
    target: pd.Period,
    anchor_periods: list[pd.Period],
    window: Sequence[pd.Period],
) -> tuple[pd.Series, str, str, dict[str, object]]:
    source = group.loc[target].copy()
    coverage = max(_coverage(source), 1.0)
    factor = min(100.0 / coverage, 12.0)
    uploaded_total = _series_number(source, "Total kWh", 0.0) or 0.0
    scaled_total = uploaded_total * factor

    trend_row, trend_method, trend_confidence, diagnostics = _estimate_row(group, target, anchor_periods, window)
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
        f"Partial-month blend using {coverage:.0f}% actual coverage and seasonal estimate; "
        f"excluded from strong anchors. {trend_method}. {profile_note}"
    )
    diagnostics = dict(diagnostics)
    diagnostics["final_estimate"] = _series_number(row, "Total kWh", None)
    diagnostics["coverage"] = coverage
    return row, method, confidence, diagnostics


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
    diagnostics: Mapping[str, object] | None = None,
    quality_notes: str = "Monthly summary estimate only; no interval data was generated.",
    coverage: float | None = None,
) -> dict[str, object]:
    diagnostics = diagnostics or {}
    seasonal_refs = diagnostics.get("seasonal_refs", []) or []
    local_refs = diagnostics.get("local_refs", []) or []
    same_month_ref = diagnostics.get("same_month_ref", "") or ""
    seasonal_estimate = diagnostics.get("seasonal_estimate", np.nan)
    local_estimate = diagnostics.get("local_estimate", np.nan)
    final_estimate = diagnostics.get("final_estimate", np.nan)
    candidate_diagnostics = diagnostics.get("candidate_diagnostics", "") or ""
    month_label = period.strftime("%B %Y")
    return {
        "Account": account,
        "Estimated month": month_label,
        "Year": int(period.year),
        "Uploaded months": _period_names(actual_periods),
        "Missing months": _period_names(affected_periods),
        "Seasonal reference months": _period_names(seasonal_refs),
        "Local reference months": _period_names(local_refs),
        "Same-month reference": same_month_ref,
        "Seasonal estimate": seasonal_estimate,
        "Local estimate": local_estimate,
        "Final blended estimate": final_estimate,
        "Estimate Method": method,
        "Confidence": confidence,
        "Coverage percentage": coverage if coverage is not None else diagnostics.get("coverage", np.nan),
        "Quality Notes": quality_notes,
        "Candidate Diagnostics": candidate_diagnostics,
        # Legacy columns retained for workbook compatibility.
        "Account number": account,
        "Estimated months": month_label,
        "Estimate method": method,
        "Confidence level": confidence,
        "Notes or warnings": quality_notes,
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
                row, method, confidence, diagnostics = _partial_scaled_row(account_group, period, anchor_periods, window)
                row["Data Source"] = "Estimated"
                row["Estimate Method"] = method
                row["Confidence"] = confidence
                row["Peak During Non-Operating"] = False
                _apply_quality(row, "Estimated", "Partial Estimated", confidence, method)
                notes.append(_note(account, period, actual_periods, affected_periods, method, confidence, diagnostics, str(row.get("Quality Notes", "")), _coverage(row)))
            else:
                row, method, confidence, diagnostics = _estimate_row(account_group, period, anchor_periods, window)
                profile_note = _apply_energy_profile(row, account_group, period, anchor_periods)
                method = f"{method}. {profile_note}"
                row["Data Source"] = "Estimated"
                row["Estimate Method"] = method
                row["Confidence"] = confidence
                row["Peak During Non-Operating"] = False
                row["Coverage %"] = np.nan
                row["Coverage Status"] = "Estimated Missing"
                row["Total Rows"] = 0
                row["Operating Rows"] = 0
                row["Not Operating Rows"] = 0
                row["Uploaded Row Count"] = 0
                if "Expected Row Count" not in row or pd.isna(row.get("Expected Row Count")):
                    row["Expected Row Count"] = np.nan
                _apply_quality(row, "Estimated", "Estimated Missing", confidence, method)
                diagnostics = dict(diagnostics)
                diagnostics["final_estimate"] = _series_number(row, "Total kWh", None)
                notes.append(_note(account, period, actual_periods, affected_periods, method, confidence, diagnostics, str(row.get("Quality Notes", ""))))

            row["Account"] = account
            row["Year"] = int(period.year)
            row["Month"] = int(period.month)
            rows.append(row)

        completed_groups.append(pd.DataFrame(rows))

    completed = pd.concat(completed_groups, ignore_index=True)
    completed = completed.sort_values(["Account", "Year", "Month"]).reset_index(drop=True)
    return completed, pd.DataFrame(notes, columns=NOTE_COLUMNS)
