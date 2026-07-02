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
    suggested_report_end_options,
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

st.header("Step 2: Configure Operating Schedule")
shifts, _ = configure_schedule()
if not any(shift["active"] for shift in shifts) or not any(shift["days"] for shift in shifts if shift["active"]):
    st.warning("At least one active shift and one operating day are required.")

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
    suggested_options = suggested_report_end_options(extracted_files, interval_overrides)
    suggested_end = suggested_options[0][0] if suggested_options else suggested_report_end(extracted_files, interval_overrides)
    report_windows = {}
    if suggested_end is not None:
        report_end_period = suggested_end
        if len(suggested_options) > 1:
            labels = [f"{format_window(report_window(period))} ({reason})" for period, reason in suggested_options]
            selected = st.radio("Detected likely report periods", labels, index=0)
            report_end_period = suggested_options[labels.index(selected)][0]
        suggested_window = report_window(report_end_period)
        st.info(f"Detected report period: {format_window(suggested_window)}.")
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

    active_schedule = any(shift["active"] and shift["days"] and shift.get("valid", True) for shift in shifts)
    can_generate = not validation_errors and confirm_estimation and active_schedule
    if st.button("Generate Dashboard", type="primary", disabled=not can_generate):
        try:
            interval_data, actual_summary = process_files(
                extracted_files, shifts, demand_selections, interval_overrides
            )
            complete_summary, estimation_notes = estimate_missing_months(actual_summary, report_windows)
            report = create_excel_report(complete_summary, file_log, estimation_notes)
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
    st.dataframe(rounded_summary(summary), use_container_width=True, hide_index=True)

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
