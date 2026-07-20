"""Read and inspect uploaded FPL Excel workbooks."""

from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
from typing import Sequence

import numpy as np
import pandas as pd

from .utils import normalize_name


SUPPORTED_INTERVAL_MINUTES = np.array([15.0, 30.0, 60.0])
TIMESTAMP_COMBO_SEPARATOR = " + "

TIMESTAMP_NAME_HINTS = {
    "date",
    "time",
    "datetime",
    "timestamp",
    "readingtime",
    "intervalstart",
    "intervalend",
    "intervaldatetime",
    "dateandtime",
}
VALUE_NAME_HINTS = {
    "demandkw",
    "demand",
    "kw",
    "load",
    "intervalload",
    "usage",
    "consumption",
    "consumptionrecorded",
    "energy",
    "energyusage",
    "usagerecorded",
    "recordedusage",
    "meterreading",
    "reading",
    "intervalvalue",
    "intervaldata",
    "kwh",
}
VALUE_HINT_TOKENS = (
    "demand",
    "kw",
    "kwh",
    "load",
    "usage",
    "consumption",
    "energy",
    "reading",
    "intervalvalue",
    "intervaldata",
)
NON_VALUE_HINT_TOKENS = (
    "account",
    "meter",
    "premise",
    "service",
    "serial",
    "number",
    "id",
    "zip",
    "phone",
)


@dataclass
class ColumnDetection:
    column: str
    confidence: str
    score: float
    reason: str


@dataclass
class ExtractedFile:
    account: str
    filename: str
    dataframe: pd.DataFrame | None = None
    timestamp_column: str | None = None
    demand_columns: list[str] = field(default_factory=list)
    numeric_columns: list[str] = field(default_factory=list)
    detected_interval_hours: float | None = None
    month: int | None = None
    year: int | None = None
    row_count: int = 0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    timestamp_candidates: list[str] = field(default_factory=list)
    demand_candidates: list[str] = field(default_factory=list)
    timestamp_detection_confidence: str = "Not detected"
    demand_detection_confidence: str = "Not detected"
    parser_notes: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.errors:
            return "Error"
        if self.warnings:
            return "Warning"
        return "Valid"


@dataclass
class _TimestampDetectionResult:
    timestamps: pd.Series | None
    column: str | None
    confidence: str
    candidates: list[ColumnDetection]


@dataclass
class _ValueDetectionResult:
    columns: list[str]
    numeric_columns: list[str]
    confidence: str
    candidates: list[ColumnDetection]


def _confidence(score: float, margin: float = 0.0, high: float = 90.0, medium: float = 70.0) -> str:
    if score >= high and margin >= 12:
        return "High"
    if score >= medium and margin >= 5:
        return "Medium"
    if score > 0:
        return "Low"
    return "Not detected"


def _name_hint_score(name: str, exact_hints: set[str], token_hints: Sequence[str] = ()) -> float:
    if name in exact_hints:
        return 35.0
    return 22.0 if any(token in name for token in token_hints) else 0.0


def detect_interval_hours(timestamps: pd.Series) -> tuple[float | None, str | None]:
    """Detect 15-, 30-, or 60-minute data from timestamp spacing."""
    clean = pd.to_datetime(timestamps, errors="coerce").dropna().sort_values().drop_duplicates()
    differences = clean.diff().dropna().dt.total_seconds().div(60)
    differences = differences[(differences > 0) & (differences <= 180)]
    if differences.empty:
        return None, "Not enough timestamps to detect the interval."

    median = float(differences.median())
    nearest = float(SUPPORTED_INTERVAL_MINUTES[np.argmin(np.abs(SUPPORTED_INTERVAL_MINUTES - median))])
    if abs(median - nearest) > 1.0:
        return None, f"Timestamp spacing ({median:g} minutes) is not a supported interval."

    consistency = float((np.abs(differences - nearest) <= 1.0).mean())
    warning = None
    if consistency < 0.9:
        warning = f"Only {consistency:.0%} of timestamp gaps match the detected interval."
    return nearest / 60.0, warning


def detect_month_year(timestamps: pd.Series) -> tuple[int | None, int | None, str | None]:
    """Assign one reporting month to a normal monthly billing-period file.

    FPL exports commonly cross a calendar-month boundary. A two-calendar-month
    span of up to 45 days is therefore normal and does not produce a warning.
    """
    clean = pd.to_datetime(timestamps, errors="coerce").dropna().sort_values()
    if clean.empty:
        return None, None, "No valid timestamps were found."

    periods = clean.dt.to_period("M")
    counts = periods.value_counts(sort=False)
    highest_count = counts.max()
    candidates = sorted(counts[counts == highest_count].index)
    midpoint_period = clean.iloc[len(clean) // 2].to_period("M")
    dominant = midpoint_period if midpoint_period in candidates else candidates[-1]

    span_days = (clean.iloc[-1] - clean.iloc[0]).total_seconds() / 86_400
    warning = None
    if len(counts) > 2 or span_days > 45:
        warning = (
            f"The file spans {len(counts)} calendar months and {span_days:.1f} days; "
            "verify that it represents one reporting period."
        )
    return int(dominant.month), int(dominant.year), warning


def _timestamp_parse_candidates(frame: pd.DataFrame) -> list[tuple[str, pd.Series]]:
    candidates: list[tuple[str, pd.Series]] = []
    normalized = {str(column): normalize_name(column) for column in frame.columns}
    columns_by_name = {name: column for column, name in normalized.items()}

    date_columns = [column for column, name in normalized.items() if name in {"date", "intervaldate"} or name.endswith("date")]
    time_columns = [column for column, name in normalized.items() if name in {"time", "intervaltime"} or name.endswith("time")]
    for date_column in date_columns:
        for time_column in time_columns:
            if date_column == time_column:
                continue
            combined = frame[date_column].astype(str).str.strip() + " " + frame[time_column].astype(str).str.strip()
            candidates.append((f"{date_column}{TIMESTAMP_COMBO_SEPARATOR}{time_column}", pd.to_datetime(combined, errors="coerce")))

    preferred_order = [
        "datetime",
        "timestamp",
        "dateandtime",
        "intervaldatetime",
        "readingtime",
        "intervalstart",
        "intervalend",
        "date",
    ]
    seen = {label for label, _ in candidates}
    for hint in preferred_order:
        column = columns_by_name.get(hint)
        if column and column not in seen:
            candidates.append((column, pd.to_datetime(frame[column], errors="coerce")))
            seen.add(column)

    for column in frame.columns:
        column = str(column)
        if column in seen:
            continue
        name = normalize_name(column)
        if "date" in name or "timestamp" in name or name in {"readingtime", "intervalstart", "intervalend"}:
            candidates.append((column, pd.to_datetime(frame[column], errors="coerce")))
            seen.add(column)
    return candidates


def _score_timestamps(frame: pd.DataFrame) -> _TimestampDetectionResult:
    detections: list[tuple[ColumnDetection, pd.Series]] = []
    for label, parsed in _timestamp_parse_candidates(frame):
        valid_ratio = float(parsed.notna().mean()) if len(parsed) else 0.0
        if valid_ratio < 0.5:
            continue
        clean = parsed.dropna().sort_values()
        unique_ratio = float(clean.nunique() / len(clean)) if len(clean) else 0.0
        interval, _ = detect_interval_hours(clean)
        name = normalize_name(label)
        hint_score = _name_hint_score(name, TIMESTAMP_NAME_HINTS, ("date", "time", "timestamp"))
        score = valid_ratio * 55 + unique_ratio * 10 + hint_score + (20 if interval is not None else 0)
        reason_parts = [f"{valid_ratio:.0%} valid datetimes"]
        if interval is not None:
            reason_parts.append("interval spacing looks consistent")
        if hint_score:
            reason_parts.append("header looks timestamp-related")
        detections.append((ColumnDetection(label, "", score, "; ".join(reason_parts)), parsed))

    detections.sort(key=lambda item: item[0].score, reverse=True)
    if not detections:
        return _TimestampDetectionResult(None, None, "Not detected", [])
    best, timestamps = detections[0]
    second_score = detections[1][0].score if len(detections) > 1 else 0.0
    confidence = _confidence(best.score, best.score - second_score)
    best.confidence = confidence
    candidates = []
    for detection, _ in detections[:8]:
        if not detection.confidence:
            detection.confidence = _confidence(detection.score, 0.0)
        candidates.append(detection)
    return _TimestampDetectionResult(timestamps, best.column, confidence, candidates)


def _parse_manual_timestamps(frame: pd.DataFrame, selection: str | None) -> tuple[pd.Series | None, str | None]:
    if not selection:
        return None, None
    if TIMESTAMP_COMBO_SEPARATOR in selection:
        parts = selection.split(TIMESTAMP_COMBO_SEPARATOR, maxsplit=1)
        if len(parts) == 2 and all(part in frame.columns for part in parts):
            combined = frame[parts[0]].astype(str).str.strip() + " " + frame[parts[1]].astype(str).str.strip()
            return pd.to_datetime(combined, errors="coerce"), selection
    if selection in frame.columns:
        return pd.to_datetime(frame[selection], errors="coerce"), selection
    return None, None


def _score_value_columns(frame: pd.DataFrame, timestamp_column: str | None = None) -> _ValueDetectionResult:
    timestamp_parts = set((timestamp_column or "").split(TIMESTAMP_COMBO_SEPARATOR))
    timestamp_candidate_names = {candidate.column for candidate in _score_timestamps(frame).candidates}
    detections: list[ColumnDetection] = []
    numeric_columns: list[str] = []

    for column in frame.columns:
        column = str(column)
        if column in timestamp_parts or column in timestamp_candidate_names:
            continue
        series = frame[column]
        numeric = pd.to_numeric(series, errors="coerce")
        numeric_ratio = float(numeric.notna().mean()) if len(numeric) else 0.0
        nonblank_ratio = float(series.notna().mean()) if len(series) else 0.0
        datetime_ratio = 0.0 if numeric_ratio >= 0.8 else (float(pd.to_datetime(series, errors="coerce").notna().mean()) if len(series) else 0.0)
        if numeric_ratio >= 0.7:
            numeric_columns.append(column)
        if numeric_ratio < 0.55 or datetime_ratio >= 0.7:
            continue

        clean = numeric.dropna()
        name = normalize_name(column)
        hint_score = _name_hint_score(name, VALUE_NAME_HINTS, VALUE_HINT_TOKENS)
        non_value_penalty = 35.0 if any(token in name for token in NON_VALUE_HINT_TOKENS) and not any(token in name for token in VALUE_HINT_TOKENS) else 0.0
        unique_ratio = float(clean.nunique() / len(clean)) if len(clean) else 0.0
        nonnegative_ratio = float((clean >= 0).mean()) if len(clean) else 0.0
        median_abs = float(clean.abs().median()) if len(clean) else 0.0
        magnitude_score = 10.0 if 0 <= median_abs <= 50_000 else -15.0
        integer_id_penalty = 15.0 if clean.nunique() <= max(3, len(clean) * 0.05) and median_abs > 10_000 else 0.0
        score = (
            numeric_ratio * 35
            + nonblank_ratio * 15
            + min(unique_ratio, 1.0) * 12
            + nonnegative_ratio * 8
            + hint_score
            + magnitude_score
            - non_value_penalty
            - integer_id_penalty
        )
        reason_parts = [f"{numeric_ratio:.0%} numeric", f"{nonblank_ratio:.0%} nonblank"]
        if hint_score:
            reason_parts.append("header matches interval-value hints")
        if non_value_penalty:
            reason_parts.append("penalized as identifier-like")
        detections.append(ColumnDetection(column, "", score, "; ".join(reason_parts)))

    detections.sort(key=lambda item: item.score, reverse=True)
    if not detections:
        return _ValueDetectionResult([], numeric_columns, "Not detected", [])
    best = detections[0]
    second_score = detections[1].score if len(detections) > 1 else 0.0
    confidence = _confidence(best.score, best.score - second_score)
    best.confidence = confidence
    candidates = []
    for detection in detections[:8]:
        if not detection.confidence:
            detection.confidence = _confidence(detection.score, 0.0)
        candidates.append(detection)
    auto_columns = [best.column] if confidence in {"High", "Medium"} else []
    return _ValueDetectionResult(auto_columns, numeric_columns, confidence, candidates)


def detect_demand_columns(frame: pd.DataFrame, timestamp_column: str | None = None) -> tuple[list[str], list[str]]:
    """Backward-compatible wrapper returning detected interval-value and numeric columns."""
    detection = _score_value_columns(frame, timestamp_column)
    return detection.columns, detection.numeric_columns


def _manual_timestamp_options(frame: pd.DataFrame, timestamp_detection: _TimestampDetectionResult) -> list[str]:
    options = [candidate.column for candidate in timestamp_detection.candidates]
    for label, _ in _timestamp_parse_candidates(frame):
        if label not in options:
            options.append(label)
    for column in map(str, frame.columns):
        if column not in options:
            options.append(column)
    return options


def _read_best_sheet(content: bytes) -> tuple[pd.DataFrame, _TimestampDetectionResult]:
    errors: list[str] = []
    fallback: tuple[pd.DataFrame, _TimestampDetectionResult] | None = None
    for header_row in (3, 0, 1, 2, 4, 5):
        try:
            frame = pd.read_excel(BytesIO(content), header=header_row)
        except Exception as exc:  # Try alternate header positions before failing.
            errors.append(str(exc))
            continue
        frame = frame.dropna(how="all").dropna(axis=1, how="all")
        if frame.empty:
            continue
        timestamp_detection = _score_timestamps(frame)
        if timestamp_detection.confidence in {"High", "Medium"}:
            return frame, timestamp_detection
        if fallback is None or len(frame) > len(fallback[0]):
            fallback = (frame, timestamp_detection)
    if fallback is not None:
        return fallback
    if errors:
        raise ValueError(f"Excel file could not be read: {errors[0]}")
    raise ValueError("No usable worksheet was found.")


def _populate_result_from_detection(
    result: ExtractedFile,
    frame: pd.DataFrame,
    timestamp_detection: _TimestampDetectionResult,
    demand_columns: Sequence[str] | None = None,
) -> ExtractedFile:
    result.dataframe = frame.copy()
    result.row_count = len(frame)
    if result.row_count < 24:
        result.warnings.append("The file has fewer than 24 data rows.")

    result.timestamp_candidates = _manual_timestamp_options(frame, timestamp_detection)
    result.timestamp_column = timestamp_detection.column
    result.timestamp_detection_confidence = timestamp_detection.confidence
    if timestamp_detection.candidates:
        best = timestamp_detection.candidates[0]
        result.parser_notes.append(f"Timestamp: selected {best.column} ({best.confidence}; {best.reason}).")

    timestamps = timestamp_detection.timestamps
    if timestamps is not None and timestamp_detection.column is not None:
        result.dataframe["__timestamp__"] = timestamps
        valid_timestamp_ratio = float(timestamps.notna().mean())
        if valid_timestamp_ratio < 0.95:
            result.warnings.append(f"{1 - valid_timestamp_ratio:.1%} of rows have invalid timestamps.")
        if timestamp_detection.confidence == "Low":
            result.warnings.append("Timestamp column detection confidence is low; verify or override the selected column.")
        result.month, result.year, month_warning = detect_month_year(timestamps)
        if month_warning:
            result.warnings.append(month_warning)
        result.detected_interval_hours, interval_warning = detect_interval_hours(timestamps)
        if interval_warning:
            result.warnings.append(interval_warning)
    else:
        result.warnings.append("A timestamp column was not detected automatically; select one manually.")

    if result.month is None:
        result.errors.append("The reporting month could not be identified.")

    value_detection = _score_value_columns(frame, result.timestamp_column)
    result.numeric_columns = value_detection.numeric_columns
    result.demand_candidates = [candidate.column for candidate in value_detection.candidates]
    if demand_columns:
        result.demand_columns = list(demand_columns)
        result.demand_detection_confidence = "Manual override"
        result.parser_notes.append("Interval value: selected " + ", ".join(result.demand_columns) + " (manual override).")
    else:
        result.demand_columns = list(value_detection.columns)
        result.demand_detection_confidence = value_detection.confidence
        if value_detection.candidates:
            best_value = value_detection.candidates[0]
            result.parser_notes.append(f"Interval value: selected {best_value.column} ({best_value.confidence}; {best_value.reason}).")
    if not result.demand_columns:
        result.warnings.append("An interval value column was not detected automatically; select one manually.")
    return result


def extract_excel_file(content: bytes, filename: str, account: str) -> ExtractedFile:
    result = ExtractedFile(account=account, filename=filename)
    try:
        frame, timestamp_detection = _read_best_sheet(content)
    except Exception as exc:
        result.errors.append(str(exc))
        return result
    return _populate_result_from_detection(result, frame, timestamp_detection)


def apply_column_overrides(
    item: ExtractedFile,
    timestamp_column: str | None = None,
    interval_value_columns: Sequence[str] | None = None,
) -> ExtractedFile:
    """Return a fresh ExtractedFile after applying user-selected parser columns."""
    if item.dataframe is None:
        return item
    frame = item.dataframe.drop(columns=["__timestamp__"], errors="ignore").copy()
    timestamps, selected_timestamp = _parse_manual_timestamps(frame, timestamp_column or item.timestamp_column)
    if timestamps is None:
        timestamp_detection = _score_timestamps(frame)
    else:
        timestamp_detection = _TimestampDetectionResult(
            timestamps=timestamps,
            column=selected_timestamp,
            confidence="Manual override" if timestamp_column and timestamp_column != item.timestamp_column else item.timestamp_detection_confidence,
            candidates=[ColumnDetection(selected_timestamp or "", "Manual override", 999.0, "selected by user")],
        )
    result = ExtractedFile(account=item.account, filename=item.filename)
    selected_values = [column for column in (interval_value_columns or item.demand_columns) if column in frame.columns]
    return _populate_result_from_detection(result, frame, timestamp_detection, selected_values)
