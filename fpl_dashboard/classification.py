"""Operating-schedule, idle-load, and on/off-peak classification."""

from __future__ import annotations

from datetime import time
from typing import Iterable, Mapping

import pandas as pd


Shift = Mapping[str, object]
ClassificationOptions = Mapping[str, object]

DEFAULT_CLASSIFICATION_OPTIONS: dict[str, object] = {
    "operating_mode": "fixed_schedule",
    "idle_quantile": 0.15,
    "timestamp_alignment": "start",
    "on_peak_rule": "exact",
}


def normalize_classification_options(options: ClassificationOptions | None = None) -> dict[str, object]:
    result = DEFAULT_CLASSIFICATION_OPTIONS.copy()
    if options:
        result.update({key: value for key, value in options.items() if value is not None})
    result["operating_mode"] = str(result.get("operating_mode", "fixed_schedule"))
    result["timestamp_alignment"] = str(result.get("timestamp_alignment", "start"))
    result["on_peak_rule"] = str(result.get("on_peak_rule", "exact"))
    try:
        quantile = float(result.get("idle_quantile", 0.15))
    except (TypeError, ValueError):
        quantile = 0.15
    result["idle_quantile"] = min(max(quantile, 0.01), 0.50)
    return result


def _active_shift(shift: Shift) -> bool:
    return bool(shift.get("active", True))


def timestamp_in_shift(timestamp: pd.Timestamp, shift: Shift) -> bool:
    """Return whether a timestamp falls in a shift, including after-midnight hours.

    Overnight hours after midnight belong to the day on which the shift started.
    End times are exclusive to avoid double-counting adjacent shifts.
    """
    if pd.isna(timestamp) or not _active_shift(shift):
        return False

    start = shift["start"]
    end = shift["end"]
    days = {int(day) for day in shift.get("days", [])}
    if not isinstance(start, time) or not isinstance(end, time):
        return False

    current_time = timestamp.time()
    weekday = timestamp.weekday()

    if start == end:  # An equal start/end is an explicit 24-hour shift.
        return weekday in days
    if start < end:
        return weekday in days and start <= current_time < end

    previous_weekday = (weekday - 1) % 7
    return (weekday in days and current_time >= start) or (
        previous_weekday in days and current_time < end
    )


def is_around_the_clock_schedule(shifts: Iterable[Shift]) -> bool:
    active_days: set[int] = set()
    for shift in shifts:
        if not _active_shift(shift):
            continue
        start = shift.get("start")
        end = shift.get("end")
        days = {int(day) for day in shift.get("days", [])}
        if isinstance(start, time) and isinstance(end, time) and start == end:
            active_days.update(days)
    return active_days == set(range(7))


def is_operating(timestamp: pd.Timestamp, shifts: Iterable[Shift]) -> bool:
    return any(timestamp_in_shift(timestamp, shift) for shift in shifts)


def classify_operating(timestamps: pd.Series, shifts: Iterable[Shift]) -> pd.Series:
    shifts = list(shifts)
    return timestamps.apply(lambda value: is_operating(pd.Timestamp(value), shifts)).astype(bool)


def idle_load_threshold(demand: pd.Series, quantile: float = 0.15) -> float:
    """Return a robust automatic idle-load threshold for continuous facilities."""
    clean = pd.to_numeric(demand, errors="coerce").dropna()
    if clean.empty:
        return 0.0
    return float(clean.quantile(min(max(float(quantile), 0.01), 0.50)))


def classify_idle_load(demand: pd.Series, quantile: float = 0.15) -> pd.Series:
    threshold = idle_load_threshold(demand, quantile)
    return (pd.to_numeric(demand, errors="coerce").fillna(0.0) > threshold).astype(bool)


def apply_timestamp_alignment(timestamps: pd.Series, interval_hours: pd.Series | float, alignment: str = "start") -> pd.Series:
    """Return timestamps adjusted to the point used for classification.

    ``start`` preserves the timestamp as shown. ``midpoint`` shifts forward by
    half of the interval, assuming the timestamp marks the interval start.
    ``end`` shifts backward by one full interval, assuming the timestamp marks
    the interval end.
    """
    result = pd.to_datetime(timestamps, errors="coerce")
    if isinstance(interval_hours, pd.Series):
        hours = pd.to_numeric(interval_hours, errors="coerce").fillna(0.0)
    else:
        hours = pd.Series(float(interval_hours), index=result.index)

    if alignment == "midpoint":
        return result + pd.to_timedelta(hours / 2, unit="h")
    if alignment == "end":
        return result - pd.to_timedelta(hours, unit="h")
    return result


def _time_between(current: time, start: time, end: time) -> bool:
    return start <= current < end


def is_on_peak(timestamp: pd.Timestamp, rule: str = "exact") -> bool:
    """Apply FPL-style seasonal on-peak windows.

    The exact rule uses interval boundaries instead of whole-hour shortcuts:
    summer weekdays from 12:00 PM to before 9:00 PM, and winter weekdays from
    6:00-10:00 AM plus 6:00-10:00 PM. Weekends are off-peak.
    """
    if pd.isna(timestamp) or timestamp.weekday() >= 5:
        return False

    if rule == "legacy_whole_hour":
        hour = timestamp.hour
        if 4 <= timestamp.month <= 10:
            return 12 <= hour <= 21
        return 6 <= hour <= 10 or 18 <= hour <= 22

    current_time = timestamp.time()
    if 4 <= timestamp.month <= 10:
        return _time_between(current_time, time(12, 0), time(21, 0))
    return _time_between(current_time, time(6, 0), time(10, 0)) or _time_between(
        current_time, time(18, 0), time(22, 0)
    )


def classify_on_peak(timestamps: pd.Series, rule: str = "exact") -> pd.Series:
    return timestamps.apply(lambda value: is_on_peak(pd.Timestamp(value), rule=rule)).astype(bool)
