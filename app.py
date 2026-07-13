"""Streamlit entry point for the ITAC FPL Dashboard Analysis Tool."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from fpl_dashboard.charts import dashboard_charts
from fpl_dashboard.estimation import estimate_missing_months
from fpl_dashboard.extraction import extract_excel_file
from fpl_dashboard.processing import find_potential_issues, process_files
from fpl_dashboard.report_period import (
    account_report_windows,
    coverage_by_account_period,
    format_window,
    partial_period_warnings,
    report_window,
    suggested_report_end,
)
from fpl_dashboard.reporting import create_excel_report
from fpl_dashboard.schedule_ui import configure_schedule
from fpl_dashboard.utils import INTERVAL_LABELS, interval_label
from fpl_dashboard.validation import missing_months_for_windows, validate_files


st.set_page_config(page_title="ITAC FPL Dashboard Analysis Tool", page_icon="📊", layout="wide")

ASSET_PATH = Path(__file__).parent / "assets" / "Logo-University-of-Miami.jpg"

def rounded_summary(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result.insert(
        3,
        "Month / Year",
        pd.to_datetime(dict(year=result["Year"], month=result["Month"], day=1)).dt.strftime("%B %Y"),
    )
    for column in result.columns:
        if column.endswith("kWh") or column.endswith("kW"):
            result[column] = pd.to_numeric(result[column], errors="coerce").round(0)
        elif column.endswith("%"):
            result[column] = pd.to_numeric(result[column], errors="coerce").round(1)
    return result


def _month_list(periods: list[pd.Period]) -> str:
    return ", ".join(period.strftime("%b %Y") for period in periods) if periods else "None"


def report_coverage_preview(files, report_windows, interval_overrides) -> pd.DataFrame:
    coverage = coverage_by_account_period(files, interval_overrides)
    uploaded_by_account: dict[str, set[pd.Period]] = {}
    for item in files:
        if item.errors or item.year is None or item.month is None:
            continue
        uploaded_by_account.setdefault(item.account, set()).add(pd.Period(year=int(item.year), month=int(item.month), freq="M"))

    rows = []
    for account, window in report_windows.items():
        periods = [pd.Period(period, freq="M") for period in window]
        uploaded = uploaded_by_account.get(account, set())
        complete = [period for period in periods if period in uploaded and coverage.get((account, period), 1.0) >= 0.85]
        partial = [period for period in periods if period in uploaded and coverage.get((account, period), 1.0) < 0.85]
        missing = [period for period in periods if period not in uploaded]
        rows.append(
            {
                "Account": account,
                "Report period": format_window(periods),
                "Complete actual months": _month_list(complete),
                "Partial months to estimate": _month_list(partial),
                "Missing months to estimate": _month_list(missing),
                "Estimated month count": len(partial) + len(missing),
            }
        )
    return pd.DataFrame(rows)




def classification_preview(summary: pd.DataFrame) -> pd.DataFrame:
    if summary.empty:
        return pd.DataFrame()
    grouped = summary.groupby("Account", sort=True).agg(
        **{
            "Actual months": ("Month", "count"),
            "Total kWh": ("Total kWh", "sum"),
            "Operating kWh": ("Operating kWh", "sum"),
            "Not Operating kWh": ("Non-Operating kWh", "sum"),
            "On-Peak kWh": ("On-Peak kWh", "sum"),
            "Off-Peak kWh": ("Off-Peak kWh", "sum"),
        }
    ).reset_index()
    grouped["Operating %"] = 100 * grouped["Operating kWh"] / grouped["Total kWh"].replace(0, pd.NA)
    grouped["Not Operating %"] = 100 * grouped["Not Operating kWh"] / grouped["Total kWh"].replace(0, pd.NA)
    grouped["On-Peak %"] = 100 * grouped["On-Peak kWh"] / grouped["Total kWh"].replace(0, pd.NA)
    grouped["Off-Peak %"] = 100 * grouped["Off-Peak kWh"] / grouped["Total kWh"].replace(0, pd.NA)
    for column in grouped.columns:
        if column.endswith("kWh"):
            grouped[column] = pd.to_numeric(grouped[column], errors="coerce").round(0)
        elif column.endswith("%"):
            grouped[column] = pd.to_numeric(grouped[column], errors="coerce").round(1)
    return grouped


def has_active_operating_schedule(shifts: list[dict[str, object]]) -> bool:
    return any(
        shift.get("active", True)
        and shift.get("valid", True)
        and (shift.get("type") == "continuous_window" or bool(shift.get("days")))
        for shift in shifts
    )


def schedule_mode_label(shifts: list[dict[str, object]], schedule_rows: pd.DataFrame) -> str:
    if any(shift.get("type") == "continuous_window" for shift in shifts):
        return "Continuous operating window"
    if len(shifts) == 1 and shifts[0].get("days") == list(range(7)) and shifts[0].get("start") == shifts[0].get("end"):
        return "24/7 operation"
    if "Schedule type" in schedule_rows.columns and not schedule_rows.empty:
        return str(schedule_rows["Schedule type"].iloc[0])
    return "Fixed weekly shifts"


def schedule_diagnostics(shifts: list[dict[str, object]], schedule_rows: pd.DataFrame) -> pd.DataFrame:
    records = []
    day_labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    full_day_labels = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    for shift in shifts:
        if shift.get("type") == "continuous_window":
            start_day = full_day_labels[int(shift.get("start_day", 0))]
            end_day = full_day_labels[int(shift.get("end_day", 0))]
            records.append(
                {
                    "Shift name": shift.get("name"),
                    "Schedule type": "Continuous operating window",
                    "Days": f"{start_day} through {end_day}",
                    "Start time": shift.get("start").strftime("%I:%M %p") if shift.get("start") else "Invalid",
                    "End time": shift.get("end").strftime("%I:%M %p") if shift.get("end") else "Invalid",
                    "Active": bool(shift.get("active", True)),
                    "Valid": bool(shift.get("valid", False)),
                    "Summary": shift.get("summary", ""),
                }
            )
            continue
        records.append(
            {
                "Shift name": shift.get("name"),
                "Schedule type": "Fixed weekly shift",
                "Days": ", ".join(day_labels[day] for day in shift.get("days", [])),
                "Start time": shift.get("start").strftime("%I:%M %p") if shift.get("start") else "Invalid",
                "End time": shift.get("end").strftime("%I:%M %p") if shift.get("end") else "Invalid",
                "Active": bool(shift.get("active", True)),
                "Valid": bool(shift.get("valid", False)),
            }
        )
    return pd.DataFrame(records) if records else schedule_rows.copy()


if ASSET_PATH.exists():
    st.image(str(ASSET_PATH), width=280)
st.title("ITAC FPL Dashboard Analysis Tool")
st.write(
    "Upload FPL interval-data workbooks to create monthly and annual energy and demand summaries. "
    "The tool validates each file, applies the operating schedule, and produces a downloadable Excel report."
)

st.header("Step 1: Upload Files")
st.info(
    "You may upload fewer than 12 monthly files per account. Missing months are detected after validation "
    "and are estimated only after you explicitly confirm."
)
account_count = int(st.number_input("Number of accounts", min_value=1, max_value=20, value=1, step=1))
uploaded_by_account: list[tuple[str, list[object]]] = []
for account_index in range(account_count):
    account_name = f"Account {account_index + 1}"
    uploads = st.file_uploader(
        f"Excel files for {account_name}",
        type=["xlsx", "xls"],
        accept_multiple_files=True,
        key=f"account_files_{account_index}",
    )
    uploaded_by_account.append((account_name, list(uploads or [])))

st.header("Step 2: Operating Schedule")
shifts, schedule_rows = configure_schedule()
current_schedule_mode = schedule_mode_label(shifts, schedule_rows)

with st.expander("Advanced Settings", expanded=False):
    timestamp_alignment_label = st.selectbox(
        "Timestamp alignment for classification",
        [
            "Timestamps mark interval start",
            "Timestamps mark interval end",
        ],
        index=0,
        help=(
            "This affects how intervals near schedule boundaries are classified. "
            "Only change this if results appear shifted around start or end times."
        ),
    )
    timestamp_alignment = {
        "Timestamps mark interval start": "start",
        "Timestamps mark interval end": "end",
    }[timestamp_alignment_label]
    st.caption("On/off-peak classification uses the FPL-style time window logic currently implemented in the app.")
    on_peak_rule_label = st.selectbox(
        "On/off-peak rule",
        ["Exact FPL-style time windows", "Legacy whole-hour windows"],
        index=0,
        help="Leave this as the exact FPL-style option unless you are comparing against older prototype output.",
    )

classification_label = current_schedule_mode
classification_mode = "fixed_schedule"
idle_quantile = 0.15
classification_options = {
    "operating_mode": classification_mode,
    "idle_quantile": idle_quantile,
    "timestamp_alignment": timestamp_alignment,
    "on_peak_rule": "exact" if on_peak_rule_label == "Exact FPL-style time windows" else "legacy_whole_hour",
}
if not has_active_operating_schedule(shifts):
    st.warning("At least one active operating schedule is required before processing.")

st.header("Step 3: Confirm Detected Data")
all_uploads = [(account, upload) for account, uploads in uploaded_by_account for upload in uploads]
extracted_files = [extract_excel_file(upload.getvalue(), upload.name, account) for account, upload in all_uploads]

if not extracted_files:
    st.warning("Upload at least one Excel file to continue.")
else:
    demand_selections: dict[tuple[str, str], list[str]] = {}
    for file_index, item in enumerate(extracted_files):
        key = (item.account, item.filename)
        if len(item.demand_columns) == 1:
            demand_selections[key] = item.demand_columns
        elif item.numeric_columns:
            prompt = "Select demand column(s)" if not item.demand_columns else "Confirm demand column(s)"
            demand_selections[key] = st.multiselect(
                f"{prompt} — {item.account} / {item.filename}",
                item.numeric_columns,
                default=item.demand_columns[:1],
                key=f"demand_selection_{file_index}",
                help="Select multiple columns only when their kW values should be added into one account total.",
            )
        else:
            demand_selections[key] = []

    st.caption(
        "The data interval is how much time each row in the FPL Excel file represents. "
        "Choosing the wrong interval can affect calculated kWh."
    )
    detected_values = [item.detected_interval_hours for item in extracted_files]
    if all(value is not None for value in detected_values):
        st.success("Detected interval(s): " + ", ".join(sorted({interval_label(value) for value in detected_values})))
    else:
        st.warning("At least one interval could not be detected. Use the manual override below for that file.")

    override_intervals = st.checkbox("Manually override detected interval")
    interval_overrides: dict[tuple[str, str], float] = {}
    if override_intervals:
        labels = list(INTERVAL_LABELS)
        for file_index, item in enumerate(extracted_files):
            detected = interval_label(item.detected_interval_hours)
            default_index = labels.index(detected) if detected in labels else 0
            selected_label = st.selectbox(
                f"Data interval — {item.account} / {item.filename}",
                labels,
                index=default_index,
                key=f"interval_override_{file_index}",
            )
            interval_overrides[(item.account, item.filename)] = INTERVAL_LABELS[selected_label]

    st.subheader("Report period")
    suggested_end = suggested_report_end(extracted_files, interval_overrides)
    report_windows = {}
    if suggested_end is not None:
        report_end_period = suggested_end
        suggested_window = report_window(report_end_period)
        st.info(f"Detected report period: {format_window(suggested_window)}.")
        st.caption(
            "The app automatically uses the latest reasonably complete uploaded month as the report end. "
            "Use the override below only if the official reporting period is different."
        )
        if st.checkbox("Change detected report period"):
            assigned_periods = sorted(
                {
                    pd.Period(year=int(item.year), month=int(item.month), freq="M")
                    for item in extracted_files
                    if item.year is not None and item.month is not None and not item.errors
                }
            )
            first_option = (assigned_periods[0] - 11) if assigned_periods else suggested_end - 11
            last_option = (assigned_periods[-1] + 6) if assigned_periods else suggested_end + 6
            period_options = list(pd.period_range(start=first_option, end=last_option, freq="M"))
            period_labels = [period.strftime("%B %Y") for period in period_options]
            selected_label = st.selectbox(
                "Report end month",
                period_labels,
                index=period_options.index(suggested_end) if suggested_end in period_options else len(period_options) - 1,
                help="The report will cover the 12 months ending with this month.",
            )
            report_end_period = period_options[period_labels.index(selected_label)]
        report_windows = account_report_windows(extracted_files, report_end_period, interval_overrides=interval_overrides)
        if report_windows:
            st.caption("Report window: " + format_window(next(iter(report_windows.values()))))
    else:
        st.warning("A report period could not be detected until at least one valid dated file is uploaded.")

    file_log, validation_errors, validation_warnings = validate_files(
        extracted_files, demand_selections, interval_overrides
    )
    validation_warnings = list(dict.fromkeys(validation_warnings + partial_period_warnings(extracted_files, interval_overrides)))
    st.dataframe(file_log, use_container_width=True, hide_index=True)
    for message in validation_warnings:
        st.warning(message)
    for message in validation_errors:
        st.error(message)

    if report_windows:
        st.subheader("Report coverage preview")
        st.caption(
            "Review this before generating the workbook. Partial months are scaled/blended with nearby complete months; "
            "missing months are estimated from complete uploaded months."
        )
        st.dataframe(report_coverage_preview(extracted_files, report_windows, interval_overrides), use_container_width=True, hide_index=True)

    active_schedule = has_active_operating_schedule(shifts)
    can_preview = not validation_errors and active_schedule and bool(extracted_files)
    if st.button("Preview classification percentages", disabled=not can_preview):
        try:
            _, preview_summary = process_files(
                extracted_files,
                shifts,
                demand_selections,
                interval_overrides,
                classification_options,
            )
            st.dataframe(classification_preview(preview_summary), use_container_width=True, hide_index=True)
        except Exception as exc:
            st.error(f"Classification preview failed: {exc}")

    st.subheader("Generation readiness checklist")
    detected_months = sorted(
        {
            pd.Period(year=int(item.year), month=int(item.month), freq="M")
            for item in extracted_files
            if item.year is not None and item.month is not None and not item.errors
        }
    )
    st.write("Detected uploaded months: " + (_month_list(detected_months) if detected_months else "None"))
    st.write("Selected interval assumption: " + (", ".join(sorted({interval_label(value) for value in detected_values if value is not None})) or "Not detected"))
    st.write(f"Timestamp alignment: {timestamp_alignment_label}")
    st.write(f"Schedule mode: {classification_label}")
    st.write(f"On/off-peak rule: {on_peak_rule_label}")
    st.write("Configured schedule:")
    st.dataframe(schedule_diagnostics(shifts, schedule_rows), use_container_width=True, hide_index=True)
    confirm_readiness = st.checkbox("I reviewed the report period, interval, timestamp, and classification assumptions.")
    missing = {account: periods for account, periods in missing_months_for_windows(extracted_files, report_windows).items() if periods}
    if missing:
        for account, periods in missing.items():
            st.warning(
                f"{account} is missing {len(periods)} reporting month(s) from the selected report period: "
                + ", ".join(period.strftime("%B %Y") for period in periods)
                + "."
            )
        confirm_estimation = st.checkbox(
            "I understand that missing months will be estimated from available month trends."
        )
    else:
        confirm_estimation = True

    can_generate = not validation_errors and confirm_estimation and confirm_readiness and active_schedule
    if st.button("Generate Dashboard", type="primary", disabled=not can_generate):
        try:
            interval_data, actual_summary = process_files(
                extracted_files,
                shifts,
                demand_selections,
                interval_overrides,
                classification_options,
            )
            complete_summary, estimation_notes = estimate_missing_months(actual_summary, report_windows)
            report = create_excel_report(complete_summary, file_log, estimation_notes, interval_data)
            st.session_state["analysis_result"] = {
                "summary": complete_summary,
                "interval_data": interval_data,
                "estimation_notes": estimation_notes,
                "file_log": file_log,
                "report": report,
                "warnings": validation_warnings,
            }
        except Exception as exc:
            st.error(f"Processing failed: {exc}")

if "analysis_result" in st.session_state:
    result = st.session_state["analysis_result"]
    summary = result["summary"]
    st.header("Step 4: Generate Dashboard")
    st.subheader("Monthly Summary")
    st.caption("Estimated rows are monthly summary estimates only; no fake interval readings are created.")
    summary_display = rounded_summary(summary)
    st.dataframe(summary_display, use_container_width=True, hide_index=True)

    for title, chart, explanation in dashboard_charts(summary):
        st.subheader(title)
        st.altair_chart(chart, use_container_width=True)
        st.caption(explanation)

    st.subheader("Potential Issues Detected")
    st.caption("These are transparent screening flags, not definitive engineering findings.")
    for issue in find_potential_issues(summary):
        st.write(f"• {issue}")
    for warning in result["warnings"]:
        st.write(f"• Validation warning: {warning}")

    st.header("Step 5: Download Report")
    st.download_button(
        "Download complete Excel report",
        data=result["report"],
        file_name="ITAC_FPL_Dashboard_Report.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary",
    )
