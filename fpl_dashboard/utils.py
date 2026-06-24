"""Shared constants and small helpers."""

from __future__ import annotations

import re
from calendar import month_name
from typing import Iterable

import numpy as np
import pandas as pd


MONTH_NAMES = list(month_name)[1:]
INTERVAL_LABELS = {
    "15 minutes": 0.25,
    "30 minutes": 0.5,
    "1 hour": 1.0,
}
INTERVAL_HOURS_TO_LABEL = {value: label for label, value in INTERVAL_LABELS.items()}


def normalize_name(value: object) -> str:
    """Normalize a column name for forgiving matching."""
    return re.sub(r"[^a-z0-9]+", "", str(value).strip().lower())


def interval_label(hours: float | None) -> str:
    if hours is None or pd.isna(hours):
        return "Not detected"
    return INTERVAL_HOURS_TO_LABEL.get(float(hours), f"{float(hours) * 60:g} minutes")


def month_label(month: int, year: int | None = None) -> str:
    label = MONTH_NAMES[int(month) - 1]
    return f"{label} {int(year)}" if year is not None and not pd.isna(year) else label


def safe_ratio(numerator: pd.Series | float, denominator: pd.Series | float):
    """Divide without producing infinities."""
    result = np.divide(
        numerator,
        denominator,
        out=np.zeros_like(np.asarray(numerator, dtype=float)),
        where=np.asarray(denominator) != 0,
    )
    return result


def join_messages(messages: Iterable[str]) -> str:
    return "; ".join(dict.fromkeys(message for message in messages if message))
