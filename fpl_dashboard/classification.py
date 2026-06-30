"""Operating-schedule and legacy on/off-peak classification."""

from __future__ import annotations

from datetime import time
from typing import Iterable, Mapping

import pandas as pd


Shift = Mapping[str, object]


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


def is_on_peak(timestamp: pd.Timestamp) -> bool:
    """Apply the legacy dashboard's on-peak rule.

    This is intentionally preserved as a configurable maintenance point, not
    represented as a verified current FPL tariff rule.
    """
    if pd.isna(timestamp) or timestamp.weekday() >= 5:
        return False
    hour = timestamp.hour
    if 4 <= timestamp.month <= 10:
        return 12 <= hour <= 21
    return 6 <= hour <= 10 or 18 <= hour <= 22


def classify_on_peak(timestamps: pd.Series) -> pd.Series:
    return timestamps.apply(lambda value: is_on_peak(pd.Timestamp(value))).astype(bool)
