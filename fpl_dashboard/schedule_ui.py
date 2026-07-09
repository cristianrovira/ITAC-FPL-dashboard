"""Stateful Streamlit controls for operating-schedule configuration."""

from __future__ import annotations

from datetime import time

import pandas as pd
import streamlit as st


DAY_OPTIONS = {
    "Monday": 0,
    "Tuesday": 1,
    "Wednesday": 2,
    "Thursday": 3,
    "Friday": 4,
    "Saturday": 5,
    "Sunday": 6,
}
DAY_NAMES = list(DAY_OPTIONS)
DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DAY_ALIASES = {
    **{day.lower(): index for day, index in DAY_OPTIONS.items()},
    **{label.lower(): index for index, label in enumerate(DAY_LABELS)},
}
PRESET_OPTIONS = [
    "Standard weekly shifts",
    "Continuous operating window",
    "24/7 operation",
    "Custom day-by-day schedule",
]


def _preset_shifts(preset: str) -> list[tuple[str, time, time]]:
    if preset in {"Standard business hours", "Standard weekly shifts"}:
        return [("Shift 1", time(8), time(17))]
    if preset == "Two shifts":
        return [("Shift 1", time(6, 30), time(15)), ("Shift 2", time(15), time(23))]
    if preset == "Three shifts":
        return [
            ("Shift 1", time(6, 30), time(15)),
            ("Shift 2", time(15), time(23)),
            ("Shift 3", time(23), time(6, 30)),
        ]
    if preset == "24/7 operation":
        return [("24-hour operation", time(0), time(0))]
    return [("Shift 1", time(8), time(17))]


def _days_for_preset(preset: str) -> list[str]:
    if preset == "24/7 operation":
        return DAY_NAMES
    return DAY_NAMES[:5]


def _format_days(days: list[str] | list[int]) -> str:
    labels: list[str] = []
    for day in days:
        if isinstance(day, int):
            if 0 <= day < len(DAY_LABELS):
                labels.append(DAY_LABELS[day])
            continue
        if day in DAY_OPTIONS:
            labels.append(DAY_LABELS[DAY_OPTIONS[day]])
    return ", ".join(labels)


def _schedule_frame(preset: str, days: list[str]) -> pd.DataFrame:
    day_label = _format_days(days)
    return pd.DataFrame(
        [
            {
                "Shift name": name,
                "Days": day_label,
                "Start time": start.strftime("%I:%M %p"),
                "End time": end.strftime("%I:%M %p"),
                "Active": True,
            }
            for name, start, end in _preset_shifts(preset)
        ]
    )


def _continuous_summary(start_day: str, start: time, end_day: str, end: time) -> str:
    return f"Operating continuously from {start_day} {start.strftime('%I:%M %p')} through {end_day} {end.strftime('%I:%M %p')}."


def _continuous_window_frame(start_day: str, start: time, end_day: str, end: time) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Schedule type": "Continuous operating window",
                "Start day": start_day,
                "Start time": start.strftime("%I:%M %p"),
                "End day": end_day,
                "End time": end.strftime("%I:%M %p"),
                "Summary": _continuous_summary(start_day, start, end_day, end),
            }
        ]
    )


def _parse_single_day(value: str) -> int | None:
    normalized = value.strip().lower().rstrip(".")
    return DAY_ALIASES.get(normalized)


def _parse_day_range(value: str) -> list[int] | None:
    if "-" in value:
        start_text, end_text = value.split("-", 1)
    elif " to " in value.lower():
        start_text, end_text = value.lower().split(" to ", 1)
    else:
        return None

    start = _parse_single_day(start_text)
    end = _parse_single_day(end_text)
    if start is None or end is None:
        return None

    days = [start]
    current = start
    while current != end:
        current = (current + 1) % 7
        days.append(current)
    return days


def _parse_days(value: object) -> list[int]:
    """Parse editable day text from the schedule table into weekday numbers."""
    if value is None or pd.isna(value):
        return []
    text = str(value).strip()
    if not text:
        return []

    normalized = text.lower().strip()
    if normalized in {"all", "daily", "every day", "24/7", "24x7"}:
        return list(range(7))
    if normalized in {"weekday", "weekdays", "mon-fri", "monday-friday", "monday to friday"}:
        return list(range(5))
    if normalized in {"weekend", "weekends", "sat-sun", "saturday-sunday", "saturday to sunday"}:
        return [5, 6]

    pieces = (
        text.replace("&", ",")
        .replace("/", ",")
        .replace(";", ",")
        .replace(" and ", ",")
        .split(",")
    )
    days: list[int] = []
    for piece in pieces:
        piece = piece.strip()
        if not piece:
            continue
        range_days = _parse_day_range(piece)
        parsed = range_days if range_days is not None else [_parse_single_day(piece)]
        for day in parsed:
            if day is not None and day not in days:
                days.append(day)
    return days


def _normalize_mode(value: str) -> str:
    if value in {"Standard business hours", "Standard weekly shifts", "Two shifts", "Three shifts"}:
        return "Standard weekly shifts"
    if value == "Custom schedule":
        return "Custom day-by-day schedule"
    if value in {"Continuous: Sunday 12 PM to Friday 7 PM", "Continuous operation: Sunday 12 PM to Friday 7 PM"}:
        return "Continuous operating window"
    return value if value in PRESET_OPTIONS else "Standard weekly shifts"


def _initialize_schedule_state() -> None:
    if "schedule_preset" not in st.session_state:
        st.session_state.schedule_preset = "Standard weekly shifts"
    st.session_state.schedule_preset = _normalize_mode(st.session_state.schedule_preset)
    if "operating_days" not in st.session_state:
        st.session_state.operating_days = _days_for_preset(st.session_state.schedule_preset)
    if "configured_shift_rows" not in st.session_state:
        st.session_state.configured_shift_rows = _schedule_frame(
            st.session_state.schedule_preset,
            st.session_state.operating_days,
        )


def _load_selected_preset() -> None:
    preset = _normalize_mode(st.session_state.schedule_preset)
    st.session_state.schedule_preset = preset
    if preset == "Continuous operating window":
        st.session_state.setdefault("continuous_start_day", "Sunday")
        st.session_state.setdefault("continuous_start_time", time(12))
        st.session_state.setdefault("continuous_end_day", "Friday")
        st.session_state.setdefault("continuous_end_time", time(19))
        st.session_state.pop("configured_shifts_editor", None)
        return
    days = _days_for_preset(preset)
    st.session_state.operating_days = days
    st.session_state.configured_shift_rows = _schedule_frame(preset, days)
    st.session_state.pop("configured_shifts_editor", None)


def _apply_editor_state(rows: pd.DataFrame, editor_state: dict[str, object]) -> pd.DataFrame:
    """Merge pending data-editor changes before a callback-triggered rerun."""
    result = rows.copy()
    for row_index, changes in editor_state.get("edited_rows", {}).items():
        index = int(row_index)
        if index not in result.index:
            continue
        for column, value in changes.items():
            result.at[index, column] = value

    deleted_rows = [int(index) for index in editor_state.get("deleted_rows", [])]
    if deleted_rows:
        result = result.drop(index=deleted_rows, errors="ignore")

    added_rows = editor_state.get("added_rows", [])
    if added_rows:
        result = pd.concat([result, pd.DataFrame(added_rows)], ignore_index=True)
    return result.reset_index(drop=True)


def _mark_custom_from_table() -> None:
    editor_state = st.session_state.get("configured_shifts_editor", {})
    st.session_state.configured_shift_rows = _apply_editor_state(
        st.session_state.configured_shift_rows,
        editor_state,
    )
    st.session_state.schedule_preset = "Custom day-by-day schedule"
    st.session_state.pop("configured_shifts_editor", None)


def _coerce_time(value: object) -> time | None:
    if isinstance(value, time):
        return value
    if value is None or pd.isna(value):
        return None
    normalized = str(value).strip().lower()
    if normalized in {"midnight", "24:00", "24:00:00"}:
        return time(0)
    if normalized == "noon":
        return time(12)
    try:
        return pd.to_datetime(str(value)).time()
    except (TypeError, ValueError):
        return None


def _shift_rows_to_dicts(edited_rows: pd.DataFrame, too_many: bool) -> list[dict[str, object]]:
    shifts: list[dict[str, object]] = []
    for index, row in edited_rows.iterrows():
        start = _coerce_time(row.get("Start time"))
        end = _coerce_time(row.get("End time"))
        days = _parse_days(row.get("Days"))
        valid = start is not None and end is not None and bool(days) and not too_many
        name = str(row.get("Shift name") or f"Shift {index + 1}").strip()
        shifts.append(
            {
                "name": name,
                "type": "weekly_shift",
                "days": days,
                "start": start,
                "end": end,
                "active": bool(row.get("Active", True)),
                "valid": valid,
            }
        )
    return shifts


def _configure_continuous_window() -> tuple[list[dict[str, object]], pd.DataFrame]:
    st.subheader("Continuous Operating Window")
    st.caption(
        "Select one weekly start point and one weekly end point. The start boundary is inclusive; "
        "the end boundary is exclusive. Windows may wrap across the end of the week."
    )
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        start_day = st.selectbox("Start day", DAY_NAMES, index=6, key="continuous_start_day")
    with col2:
        start_time = st.time_input("Start time", value=st.session_state.get("continuous_start_time", time(12)), key="continuous_start_time")
    with col3:
        end_day = st.selectbox("End day", DAY_NAMES, index=4, key="continuous_end_day")
    with col4:
        end_time = st.time_input("End time", value=st.session_state.get("continuous_end_time", time(19)), key="continuous_end_time")

    summary = _continuous_summary(start_day, start_time, end_day, end_time)
    st.info(summary)
    frame = _continuous_window_frame(start_day, start_time, end_day, end_time)
    shift = {
        "name": "Continuous operating window",
        "type": "continuous_window",
        "start_day": DAY_OPTIONS[start_day],
        "start": start_time,
        "end_day": DAY_OPTIONS[end_day],
        "end": end_time,
        "active": True,
        "valid": True,
        "summary": summary,
    }
    return [shift], frame


def configure_schedule() -> tuple[list[dict[str, object]], pd.DataFrame]:
    """Render the schedule controls and return normalized schedule dictionaries."""
    _initialize_schedule_state()

    preset = st.selectbox(
        "Schedule mode",
        PRESET_OPTIONS,
        key="schedule_preset",
        on_change=_load_selected_preset,
        help=(
            "Use Standard weekly shifts for recurring day/time rows, Continuous operating window "
            "for one uninterrupted weekly span, 24/7 for always-on facilities, or Custom day-by-day schedule."
        ),
    )
    preset = _normalize_mode(preset)
    if preset == "Continuous operating window":
        return _configure_continuous_window()

    rows = st.session_state.configured_shift_rows.copy()
    if preset == "24/7 operation":
        rows = _schedule_frame(preset, DAY_NAMES)
        st.session_state.configured_shift_rows = rows.copy()

    st.subheader("Configured Shifts")
    st.caption(
        "Edit each shift's days and times directly in the table. Examples: Mon-Fri, Sat-Sun, "
        "weekdays, weekends, or 24/7. Editing a standard schedule automatically converts "
        "it to Custom day-by-day schedule."
    )
    edited_rows = st.data_editor(
        rows,
        key="configured_shifts_editor",
        hide_index=True,
        use_container_width=True,
        num_rows="dynamic" if preset == "Custom day-by-day schedule" else "fixed",
        on_change=_mark_custom_from_table if preset != "24/7 operation" else None,
        column_config={
            "Shift name": st.column_config.TextColumn("Shift name", required=True),
            "Days": st.column_config.TextColumn("Days", help="Examples: Mon-Fri, Sat-Sun, weekends, 24/7", required=True),
            "Start time": st.column_config.TextColumn("Start time", help="For example: 8:00 AM", required=True),
            "End time": st.column_config.TextColumn("End time", help="For example: 12:00 AM", required=True),
            "Active": st.column_config.CheckboxColumn("Active"),
        },
    )
    st.session_state.configured_shift_rows = edited_rows.copy()

    too_many = len(edited_rows) > 3 and preset != "24/7 operation"
    if too_many:
        st.error("A maximum of three shifts is supported. Remove extra rows before processing.")
    if edited_rows.empty:
        st.error("Add at least one configured shift before processing.")

    shifts = _shift_rows_to_dicts(edited_rows, too_many)
    if any(not shift["valid"] for shift in shifts):
        st.error("Every configured shift must have valid days plus a valid start and end time.")
    return shifts, edited_rows
