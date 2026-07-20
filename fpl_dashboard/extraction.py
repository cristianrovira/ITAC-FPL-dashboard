"""Read and inspect uploaded FPL Excel workbooks."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
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
    "hour",
    "datetime",
    "timestamp",
    "readingtime",
    "readingdate",
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
UNIT_LABELS = {
    "power_kw": "Demand/power in kW",
    "energy_kwh": "Energy per interval in kWh",
    "unknown": "Unknown - assuming demand/power in kW",
}


@dataclass
class ColumnDetection:
    column: str
    confidence: str
    score: float
    reason: str


@dataclass
class IntervalDetection:
    hours: float | None
    minutes: float | None
    confidence: str
    note: str | None = None
    usable_differences: int = 0


@dataclass
class ExtractedFile:
    account: str
    filename: str
    dataframe: pd.DataFrame | None = None
    timestamp_column: str | None = None
    demand_columns: list[str] = field(default_factory=list)
    numeric_columns: list[str] = field(default_factory=list)
    detected_interval_hours: float | None = None
    detected_interval_minutes: float | None = None
    interval_detection_confidence: str = "Not detected"
    interval_detection_note: str = ""
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
    timestamp_source: str = ""
    timestamp_valid_count: int = 0
    timestamp_unique_count: int = 0
    first_timestamp: pd.Timestamp | None = None
    last_timestamp: pd.Timestamp | None = None
    interval_value_unit: str = "power_kw"
    interval_value_unit_confidence: str = "Not detected"
    interval_value_unit_note: str = ""

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


def _coerce_date_series(series: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(series, errors="coerce")
    if parsed.notna().mean() >= 0.5:
        return parsed
    numeric = pd.to_numeric(series, errors="coerce")
    if numeric.notna().mean() >= 0.5:
        return pd.to_datetime(numeric, unit="D", origin="1899-12-30", errors="coerce")
    return parsed


def _coerce_time_value(value) -> pd.Timedelta | pd.NaT:
    if value is None or pd.isna(value):
        return pd.NaT
    if isinstance(value, pd.Timestamp):
        if pd.isna(value):
            return pd.NaT
        return pd.to_timedelta(value.hour, unit="h") + pd.to_timedelta(value.minute, unit="m") + pd.to_timedelta(value.second, unit="s")
    if isinstance(value, datetime):
        return pd.to_timedelta(value.hour, unit="h") + pd.to_timedelta(value.minute, unit="m") + pd.to_timedelta(value.second, unit="s")
    if isinstance(value, time):
        return pd.to_timedelta(value.hour, unit="h") + pd.to_timedelta(value.minute, unit="m") + pd.to_timedelta(value.second, unit="s")
    if isinstance(value, (int, float, np.integer, np.floating)):
        numeric = float(value)
        if 0 <= numeric < 1:
            return pd.to_timedelta(round(numeric * 86400), unit="s")
        if 0 <= numeric <= 24:
            return pd.to_timedelta(round(numeric * 3600), unit="s")
        return pd.NaT
    text = str(value).strip()
    if not text:
        return pd.NaT
    numeric = pd.to_numeric(pd.Series([text]), errors="coerce").iloc[0]
    if pd.notna(numeric):
        return _coerce_time_value(float(numeric))
    parsed = pd.to_datetime(pd.Series([text]), errors="coerce").iloc[0]
    if pd.isna(parsed):
        return pd.NaT
    parsed = pd.Timestamp(parsed)
    return pd.to_timedelta(parsed.hour, unit="h") + pd.to_timedelta(parsed.minute, unit="m") + pd.to_timedelta(parsed.second, unit="s")


def _coerce_time_series(series: pd.Series) -> pd.Series:
    return series.apply(_coerce_time_value)


def construct_timestamps(frame: pd.DataFrame, date_column: str, time_column: str | None = None) -> pd.Series:
    """Construct timestamps from one combined column or separate date/time columns."""
    date_values = _coerce_date_series(frame[date_column])
    if time_column is None:
        return date_values
    time_values = _coerce_time_series(frame[time_column])
    return date_values.dt.normalize() + time_values


def interval_detection(timestamps: pd.Series) -> IntervalDetection:
    """Detect interval length using the most common standard timestamp spacing."""
    clean = pd.to_datetime(timestamps, errors="coerce").dropna().sort_values().drop_duplicates()
    if clean.nunique() < 2:
        return IntervalDetection(None, None, "Not detected", "At least two distinct valid timestamps are required.", 0)
    differences = clean.diff().dropna().dt.total_seconds().div(60)
    differences = differences[(differences > 0) & (differences <= 180)]
    if differences.empty:
        return IntervalDetection(None, None, "Not detected", "No usable positive timestamp gaps were found after removing duplicates and large gaps.", 0)

    counts = {float(minutes): int((np.abs(differences - minutes) <= 1.0).sum()) for minutes in SUPPORTED_INTERVAL_MINUTES}
    best_minutes, best_count = max(counts.items(), key=lambda item: item[1])
    if best_count == 0:
        common_gap = float(differences.round().mode().iloc[0])
        return IntervalDetection(None, None, "Not detected", f"Most common timestamp spacing ({common_gap:g} minutes) is not a supported interval.", int(len(differences)))

    consistency = best_count / len(differences)
    if best_count >= 4 and consistency >= 0.8:
        confidence = "High"
    elif best_count >= 2 and consistency >= 0.5:
        confidence = "Medium"
    else:
        confidence = "Low"
    note = f"Detected {best_minutes:g}-minute spacing from {best_count} of {len(differences)} usable timestamp gaps."
    if consistency < 0.9:
        note += f" {1 - consistency:.0%} of usable gaps differ, likely due to missing rows or file boundaries."
    return IntervalDetection(best_minutes / 60.0, best_minutes, confidence, note, int(len(differences)))


def detect_interval_hours(timestamps: pd.Series) -> tuple[float | None, str | None]:
    """Backward-compatible interval detector returning hours and warning/note."""
    detected = interval_detection(timestamps)
    if detected.hours is None:
        return None, detected.note or "Not enough timestamps to detect the interval."
    warning = None if detected.confidence == "High" else detected.note
    return detected.hours, warning


def detect_month_year(timestamps: pd.Series) -> tuple[int | None, int | None, str | None]:
    """Assign one reporting month to a normal monthly billing-period file."""
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


def _date_column_names(normalized: dict[str, str]) -> list[str]:
    return [column for column, name in normalized.items() if name in {"date", "intervaldate", "readingdate"} or name.endswith("date")]


def _time_column_names(normalized: dict[str, str]) -> list[str]:
    return [column for column, name in normalized.items() if name in {"time", "hour", "intervaltime", "readingtime"} or name.endswith("time") or name.endswith("hour")]


def _timestamp_parse_candidates(frame: pd.DataFrame) -> list[tuple[str, pd.Series]]:
    candidates: list[tuple[str, pd.Series]] = []
    normalized = {str(column): normalize_name(column) for column in frame.columns}
    columns_by_name = {name: column for column, name in normalized.items()}

    date_columns = _date_column_names(normalized)
    time_columns = _time_column_names(normalized)
    seen: set[str] = set()
    for date_column in date_columns:
        date_direct = construct_timestamps(frame, date_column)
        has_time_component = bool((date_direct.dropna().dt.time != time(0, 0)).any()) if date_direct.notna().any() else False
        if has_time_component:
            candidates.append((date_column, date_direct))
            seen.add(date_column)
            continue
        for time_column in time_columns:
            if date_column == time_column:
                continue
            label = f"{date_column}{TIMESTAMP_COMBO_SEPARATOR}{time_column}"
            candidates.append((label, construct_timestamps(frame, date_column, time_column)))
            seen.add(label)

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
    for hint in preferred_order:
        column = columns_by_name.get(hint)
        if column and column not in seen:
            candidates.append((column, construct_timestamps(frame, column)))
            seen.add(column)

    for column in frame.columns:
        column = str(column)
        if column in seen:
            continue
        name = normalize_name(column)
        if "date" in name or "timestamp" in name or name in {"readingtime", "intervalstart", "intervalend"}:
            candidates.append((column, construct_timestamps(frame, column)))
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
        detected = interval_detection(clean)
        name = normalize_name(label)
        hint_score = _name_hint_score(name, TIMESTAMP_NAME_HINTS, ("date", "time", "hour", "timestamp"))
        score = valid_ratio * 50 + unique_ratio * 18 + hint_score + (25 if detected.hours is not None else 0)
        reason_parts = [f"{valid_ratio:.0%} valid datetimes", f"{clean.nunique()} unique timestamps"]
        if detected.hours is not None:
            reason_parts.append(f"{detected.minutes:g}-minute interval spacing looks consistent")
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
            return construct_timestamps(frame, parts[0], parts[1]), selection
    if selection in frame.columns:
        return construct_timestamps(frame, selection), selection
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


def infer_interval_value_unit(column: str | None, interval_hours: float | None = None) -> tuple[str, str, str]:
    """Infer whether uploaded interval values are power kW or interval energy kWh."""
    name = normalize_name(column or "")
    if "kwh" in name or any(token in name for token in ("energy", "consumption", "usage")):
        return "energy_kwh", "High", "Header indicates uploaded values are interval energy/usage; kWh is not multiplied by interval duration."
    if "kw" in name or "demand" in name or "load" in name:
        return "power_kw", "High", "Header indicates uploaded values are demand/power; kWh is calculated as kW times interval hours."
    return "power_kw", "Low", "Could not confidently determine units; assuming demand/power kW unless manually overridden."


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
    best: tuple[pd.DataFrame, _TimestampDetectionResult, float] | None = None
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
        score = timestamp_detection.candidates[0].score if timestamp_detection.candidates else 0.0
        if best is None or score > best[2]:
            best = (frame, timestamp_detection, score)
    if best is not None:
        return best[0], best[1]
    if errors:
        raise ValueError(f"Excel file could not be read: {errors[0]}")
    raise ValueError("No usable worksheet was found.")


def _populate_timestamp_diagnostics(result: ExtractedFile, timestamps: pd.Series) -> None:
    clean = pd.to_datetime(timestamps, errors="coerce").dropna().sort_values()
    result.timestamp_valid_count = int(clean.size)
    result.timestamp_unique_count = int(clean.nunique())
    result.first_timestamp = pd.Timestamp(clean.iloc[0]) if not clean.empty else None
    result.last_timestamp = pd.Timestamp(clean.iloc[-1]) if not clean.empty else None


def _populate_result_from_detection(
    result: ExtractedFile,
    frame: pd.DataFrame,
    timestamp_detection: _TimestampDetectionResult,
    demand_columns: Sequence[str] | None = None,
    interval_value_unit: str | None = None,
) -> ExtractedFile:
    result.dataframe = frame.copy()
    result.row_count = len(frame)
    if result.row_count < 24:
        result.warnings.append("The file has fewer than 24 data rows.")

    result.timestamp_candidates = _manual_timestamp_options(frame, timestamp_detection)
    result.timestamp_column = timestamp_detection.column
    result.timestamp_source = timestamp_detection.column or ""
    result.timestamp_detection_confidence = timestamp_detection.confidence
    if timestamp_detection.candidates:
        best = timestamp_detection.candidates[0]
        result.parser_notes.append(f"Timestamp: selected {best.column} ({best.confidence}; {best.reason}).")

    timestamps = timestamp_detection.timestamps
    if timestamps is not None and timestamp_detection.column is not None:
        result.dataframe["__timestamp__"] = timestamps
        _populate_timestamp_diagnostics(result, timestamps)
        valid_timestamp_ratio = float(timestamps.notna().mean())
        if valid_timestamp_ratio < 0.95:
            result.warnings.append(f"{1 - valid_timestamp_ratio:.1%} of rows have invalid timestamps.")
        if timestamp_detection.confidence == "Low":
            result.warnings.append("Timestamp column detection confidence is low; verify or override the selected column.")
        result.month, result.year, month_warning = detect_month_year(timestamps)
        if month_warning:
            result.warnings.append(month_warning)
        detected_interval = interval_detection(timestamps)
        result.detected_interval_hours = detected_interval.hours
        result.detected_interval_minutes = detected_interval.minutes
        result.interval_detection_confidence = detected_interval.confidence
        result.interval_detection_note = detected_interval.note or ""
        if detected_interval.hours is None:
            result.warnings.append(detected_interval.note or "The data interval could not be detected.")
        elif detected_interval.confidence != "High":
            result.warnings.append(detected_interval.note or "The data interval was detected with limited confidence.")
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

    if interval_value_unit:
        result.interval_value_unit = interval_value_unit
        result.interval_value_unit_confidence = "Manual override"
        result.interval_value_unit_note = UNIT_LABELS.get(interval_value_unit, interval_value_unit)
    else:
        selected_column = result.demand_columns[0] if result.demand_columns else None
        unit, confidence, note = infer_interval_value_unit(selected_column, result.detected_interval_hours)
        result.interval_value_unit = unit
        result.interval_value_unit_confidence = confidence
        result.interval_value_unit_note = note
        if confidence == "Low":
            result.warnings.append(note)
    if result.interval_value_unit_note:
        result.parser_notes.append(f"Units: {UNIT_LABELS.get(result.interval_value_unit, result.interval_value_unit)} ({result.interval_value_unit_confidence}; {result.interval_value_unit_note})")
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
    interval_value_unit: str | None = None,
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
    return _populate_result_from_detection(result, frame, timestamp_detection, selected_values, interval_value_unit)
