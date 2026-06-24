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
PRESET_OPTIONS = [
    "Standard business hours",
    "Two shifts",
    "Three shifts",
    "24/7 operation",
    "Custom schedule",
]


def _preset_shifts(preset: str) -> list[tuple[str, time, time]]:
    if preset == "Standard business hours":
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
    return list(DAY_OPTIONS) if preset == "24/7 operation" else list(DAY_OPTIONS)[:5]


def _schedule_frame(preset: str, days: list[str]) -> pd.DataFrame:
    day_label = ", ".join(day[:3] for day in days)
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


def _initialize_schedule_state() -> None:
    if "schedule_preset" not in st.session_state:
        st.session_state.schedule_preset = "Standard business hours"
    if "operating_days" not in st.session_state:
        st.session_state.operating_days = _days_for_preset(st.session_state.schedule_preset)
    if "configured_shift_rows" not in st.session_state:
        st.session_state.configured_shift_rows = _schedule_frame(
            st.session_state.schedule_preset,
            st.session_state.operating_days,
        )


def _load_selected_preset() -> None:
    preset = st.session_state.schedule_preset
    days = _days_for_preset(preset)
    st.session_state.operating_days = days
    st.session_state.configured_shift_rows = _schedule_frame(preset, days)
    st.session_state.pop("configured_shifts_editor", None)


def _mark_custom_from_days() -> None:
    st.session_state.schedule_preset = "Custom schedule"
    rows = st.session_state.configured_shift_rows.copy()
    rows["Days"] = ", ".join(day[:3] for day in st.session_state.operating_days)
    st.session_state.configured_shift_rows = rows
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
    st.session_state.schedule_preset = "Custom schedule"
    st.session_state.pop("configured_shifts_editor", None)


def _coerce_time(value: object) -> time | None:
    if isinstance(value, time):
        return value
    if value is None or pd.isna(value):
        return None
    try:
        return pd.to_datetime(str(value)).time()
    except (TypeError, ValueError):
        return None


def configure_schedule() -> tuple[list[dict[str, object]], pd.DataFrame]:
    """Render the editable schedule and return normalized shift dictionaries."""
    _initialize_schedule_state()

    preset = st.selectbox(
        "Schedule preset",
        PRESET_OPTIONS,
        key="schedule_preset",
        on_change=_load_selected_preset,
        help=(
            "Presets populate default shifts. Editing a preset's operating days or shift table "
            "automatically changes the selection to Custom schedule."
        ),
    )
    selected_days = st.multiselect(
        "Operating days",
        list(DAY_OPTIONS),
        key="operating_days",
        disabled=preset == "24/7 operation",
        on_change=_mark_custom_from_days,
    )
    if preset == "24/7 operation":
        selected_days = list(DAY_OPTIONS)

    rows = st.session_state.configured_shift_rows.copy()
    rows["Days"] = ", ".join(day[:3] for day in selected_days)

    st.subheader("Configured Shifts")
    st.caption(
        "Edit a start or end time directly in the table. Any change to a preset schedule "
        "automatically converts it to Custom schedule. Custom schedules may contain up to three shifts."
    )
    edited_rows = st.data_editor(
        rows,
        key="configured_shifts_editor",
        hide_index=True,
        use_container_width=True,
        num_rows="dynamic" if preset == "Custom schedule" else "fixed",
        disabled=["Days"],
        on_change=_mark_custom_from_table,
        column_config={
            "Shift name": st.column_config.TextColumn("Shift name", required=True),
            "Days": st.column_config.TextColumn("Days"),
            "Start time": st.column_config.TextColumn("Start time", help="For example: 8:00 AM", required=True),
            "End time": st.column_config.TextColumn("End time", help="For example: 5:00 PM", required=True),
            "Active": st.column_config.CheckboxColumn("Active"),
        },
    )
    st.session_state.configured_shift_rows = edited_rows.copy()

    too_many = len(edited_rows) > 3
    if too_many:
        st.error("A maximum of three shifts is supported. Remove extra rows before processing.")
    if edited_rows.empty:
        st.error("Add at least one configured shift before processing.")

    shifts: list[dict[str, object]] = []
    for index, row in edited_rows.iterrows():
        start = _coerce_time(row.get("Start time"))
        end = _coerce_time(row.get("End time"))
        valid = start is not None and end is not None and not too_many
        name = str(row.get("Shift name") or f"Shift {index + 1}").strip()
        shifts.append(
            {
                "name": name,
                "days": [DAY_OPTIONS[day] for day in selected_days],
                "start": start,
                "end": end,
                "active": bool(row.get("Active", True)),
                "valid": valid,
            }
        )
    if any(not shift["valid"] for shift in shifts):
        st.error("Every configured shift must have a valid start and end time.")
    return shifts, edited_rows
