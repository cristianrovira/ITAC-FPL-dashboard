"""Excel report generation."""

from __future__ import annotations

from io import BytesIO

import pandas as pd

from .processing import DEMAND_COLUMNS, ENERGY_COLUMNS, daily_hourly_classification_breakdown


def consolidated_summary(monthly: pd.DataFrame) -> pd.DataFrame:
    """Aggregate account summaries for a facility-level annual view.

    Energy is additive. Account demand peaks are summed conservatively because
    estimated months do not have coincident interval readings.
    """
    rows: list[dict[str, object]] = []
    for (year, month), group in monthly.groupby(["Year", "Month"], sort=True):
        row: dict[str, object] = {"Account": "Facility Total", "Year": int(year), "Month": int(month)}
        for column in ENERGY_COLUMNS + DEMAND_COLUMNS:
            if column in group:
                row[column] = float(group[column].sum())
        row["Peak During Non-Operating"] = bool(group["Peak During Non-Operating"].fillna(False).any())
        row["Data Source"] = "Estimated" if (group["Data Source"] == "Estimated").any() else "Actual"
        row["Estimate Method"] = "Includes estimated account summaries" if row["Data Source"] == "Estimated" else "Actual uploaded interval files"
        confidence_rank = {"Very Low": 0, "Low": 1, "Medium Estimate Confidence": 2, "High Estimate Confidence": 3, "Normal": 4}
        row["Confidence"] = min(group["Confidence"], key=lambda value: confidence_rank.get(str(value), 0))
        total = float(row.get("Total kWh", 0))
        row["Non-Operating %"] = 100 * float(row.get("Non-Operating kWh", 0)) / total if total else 0.0
        rows.append(row)
    return pd.DataFrame(rows)


def _display_frame(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    if "Month" in result and "Year" in result:
        result.insert(
            result.columns.get_loc("Month"),
            "Month / Year",
            pd.to_datetime(dict(year=result["Year"], month=result["Month"], day=1)).dt.strftime("%B %Y"),
        )
    for column in result.columns:
        if column.endswith("kWh") or column.endswith("kW"):
            result[column] = pd.to_numeric(result[column], errors="coerce").round(0)
        elif column.endswith("%"):
            result[column] = pd.to_numeric(result[column], errors="coerce").round(1)
    return result


def official_dashboard_summary(monthly_display: pd.DataFrame) -> pd.DataFrame:
    result = pd.DataFrame()
    mappings = [
        ("Account", "Account"),
        ("Month / Year", "Month"),
        ("Non-Operating kWh", "Not Operating"),
        ("Operating kWh", "Operating Shift"),
        ("Total kWh", "Total kWh"),
        ("On-Peak Operating kWh", "On Peak Operating (kWh)"),
        ("Off-Peak Operating kWh", "Off Peak Operating"),
        ("On-Peak Non-Operating kWh", "On Peak Not Operating"),
        ("Off-Peak Non-Operating kWh", "Off Peak Not Operating"),
        ("Total kWh", "Total kWh Check"),
        ("Data Source", "Data Source"),
        ("Confidence", "Confidence"),
    ]
    for source, label in mappings:
        if source in monthly_display:
            result[label] = monthly_display[source]
    return result


def data_quality_summary(monthly_display: pd.DataFrame, input_file_log: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "Account",
        "Month / Year",
        "Data Source",
        "Coverage Status",
        "Coverage %",
        "Uploaded Row Count",
        "Expected Row Count",
        "Quality Score",
        "Quality Level",
        "Quality Notes",
        "Estimate Method",
        "Confidence",
    ]
    quality = monthly_display[[column for column in columns if column in monthly_display]].copy()
    if input_file_log.empty:
        return quality
    return quality


def classification_audit_summary(monthly_display: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "Account",
        "Month / Year",
        "Total Rows",
        "Operating Rows",
        "Not Operating Rows",
        "Operating kWh",
        "Non-Operating kWh",
        "On-Peak Operating kWh",
        "Off-Peak Operating kWh",
        "On-Peak Non-Operating kWh",
        "Off-Peak Non-Operating kWh",
        "Data Source",
        "Estimate Method",
        "Confidence",
    ]
    return monthly_display[[column for column in columns if column in monthly_display]].copy()


DASHBOARD_TOTAL_COLUMNS = {"Total kWh", "Total kWh Check"}


def _with_total_row(frame: pd.DataFrame, label_column: str = "Month") -> pd.DataFrame:
    """Append an Excel-display total row for dashboard-style sheets."""
    if frame.empty:
        return frame
    result = frame.copy()
    total_row: dict[str, object] = {}
    for column in result.columns:
        values = pd.to_numeric(result[column], errors="coerce")
        if values.notna().any():
            total_row[column] = float(values.sum())
        else:
            total_row[column] = ""
    if label_column in result.columns:
        total_row[label_column] = "Total"
    elif len(result.columns):
        total_row[result.columns[0]] = "Total"
    return pd.concat([result, pd.DataFrame([total_row])], ignore_index=True)


def _write_formatted_cell(worksheet, row: int, column: int, value: object, cell_format) -> None:
    if pd.isna(value):
        worksheet.write_blank(row, column, None, cell_format)
    elif isinstance(value, bool):
        worksheet.write_boolean(row, column, value, cell_format)
    elif isinstance(value, (int, float)):
        worksheet.write_number(row, column, float(value), cell_format)
    else:
        worksheet.write(row, column, value, cell_format)


def _column_width(frame: pd.DataFrame, column: str) -> int:
    values = frame[column].astype(str) if column in frame and not frame.empty else pd.Series(dtype=str)
    max_value_width = int(values.str.len().max()) if not values.empty else 0
    return min(max(len(str(column)) + 2, max_value_width + 2, 12), 45)


def _excel_column_name(index: int) -> str:
    name = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _format_official_dashboard_sheet(workbook, worksheet, frame: pd.DataFrame) -> None:
    formats = {
        "header": workbook.add_format({
            "bold": True,
            "bg_color": "#95B3D7",
            "font_color": "#000000",
            "border": 1,
            "border_color": "#000000",
            "align": "center",
            "valign": "vcenter",
            "text_wrap": True,
        }),
        "month": workbook.add_format({"bg_color": "#D8E4BC", "border": 1, "border_color": "#D9D9D9", "align": "center"}),
        "text": workbook.add_format({"border": 1, "border_color": "#D9D9D9", "align": "left"}),
        "number": workbook.add_format({"num_format": "#,##0", "border": 1, "border_color": "#D9D9D9", "align": "right"}),
        "total_label": workbook.add_format({"bold": True, "bg_color": "#D8E4BC", "border": 1, "border_color": "#000000", "align": "center"}),
        "total_number": workbook.add_format({"bold": True, "bg_color": "#D8E4BC", "num_format": "#,##0", "border": 1, "border_color": "#000000", "align": "right"}),
        "total_kwh": workbook.add_format({"bold": True, "bg_color": "#FFFF00", "num_format": "#,##0", "border": 1, "border_color": "#000000", "align": "right"}),
    }
    worksheet.freeze_panes(1, 0)
    worksheet.hide_gridlines(2)
    worksheet.set_row(0, 36)
    total_row_index = len(frame)
    for column_index, column in enumerate(frame.columns):
        worksheet.write(0, column_index, column, formats["header"])
        worksheet.set_column(column_index, column_index, _column_width(frame, column))
    for row_index, (_, row) in enumerate(frame.iterrows(), start=1):
        is_total = row_index == total_row_index
        for column_index, column in enumerate(frame.columns):
            value = row[column]
            is_numeric = pd.api.types.is_number(value) and not pd.isna(value)
            if is_total:
                if column in DASHBOARD_TOTAL_COLUMNS:
                    cell_format = formats["total_kwh"]
                elif is_numeric:
                    cell_format = formats["total_number"]
                else:
                    cell_format = formats["total_label"]
            elif column == "Month":
                cell_format = formats["month"]
            elif is_numeric:
                cell_format = formats["number"]
            else:
                cell_format = formats["text"]
            _write_formatted_cell(worksheet, row_index, column_index, value, cell_format)


def _format_standard_sheet(workbook, worksheet, frame: pd.DataFrame, sheet_name: str) -> None:
    header_format = workbook.add_format({
        "bold": True,
        "bg_color": "#95B3D7",
        "font_color": "#000000",
        "border": 1,
        "border_color": "#000000",
        "align": "center",
        "valign": "vcenter",
        "text_wrap": True,
    })
    text_format = workbook.add_format({"border": 1, "border_color": "#D9D9D9"})
    number_format = workbook.add_format({"num_format": "#,##0", "border": 1, "border_color": "#D9D9D9"})
    percent_format = workbook.add_format({"num_format": "0.0", "border": 1, "border_color": "#D9D9D9"})
    estimated_format = workbook.add_format({"bg_color": "#FFF2CC"})
    worksheet.freeze_panes(1, 0)
    worksheet.set_row(0, 30)
    worksheet.autofilter(0, 0, max(len(frame), 1), max(len(frame.columns) - 1, 0))
    for column_index, column in enumerate(frame.columns):
        worksheet.write(0, column_index, column, header_format)
        values = pd.to_numeric(frame[column], errors="coerce") if column in frame else pd.Series(dtype=float)
        if column.endswith("%"):
            column_format = percent_format
        elif values.notna().any():
            column_format = number_format
        else:
            column_format = text_format
        worksheet.set_column(column_index, column_index, _column_width(frame, column), column_format)
    if "Data Source" in frame.columns and len(frame):
        source_index = frame.columns.get_loc("Data Source")
        source_column = _excel_column_name(source_index)
        worksheet.conditional_format(
            1,
            0,
            len(frame),
            len(frame.columns) - 1,
            {
                "type": "formula",
                "criteria": f'=${source_column}2="Estimated"',
                "format": estimated_format,
            },
        )


def create_excel_report(
    monthly_summary: pd.DataFrame,
    input_file_log: pd.DataFrame,
    estimation_notes: pd.DataFrame,
    interval_data: pd.DataFrame | None = None,
) -> bytes:
    """Create the complete report workbook in memory."""
    account_count = monthly_summary["Account"].nunique() if not monthly_summary.empty else 0
    consolidated = consolidated_summary(monthly_summary) if account_count > 1 else pd.DataFrame()
    monthly_display = _display_frame(monthly_summary)

    sheets: list[tuple[str, pd.DataFrame]] = [
        ("Official Dashboard", _with_total_row(official_dashboard_summary(monthly_display))),
        ("Monthly Summary", monthly_display),
        ("Data Quality", data_quality_summary(monthly_display, input_file_log)),
        ("Classification Audit", classification_audit_summary(monthly_display)),
        (
            "Operating vs Non-Operating",
            monthly_display[[column for column in ["Account", "Month / Year", "Operating kWh", "Non-Operating kWh", "Operating Demand kW", "Non-Operating Demand kW", "Non-Operating %", "Data Source", "Confidence"] if column in monthly_display]],
        ),
        (
            "On-Peak vs Off-Peak Summary",
            monthly_display[[column for column in ["Account", "Month / Year", "On-Peak kWh", "Off-Peak kWh", "On-Peak Demand kW", "Off-Peak Demand kW", "Data Source", "Confidence"] if column in monthly_display]],
        ),
        (
            "Demand Summary",
            monthly_display[[column for column in ["Account", "Month / Year", "Peak Demand kW", "Operating Demand kW", "Non-Operating Demand kW", "On-Peak Demand kW", "Off-Peak Demand kW", "Peak During Non-Operating", "Data Source", "Confidence"] if column in monthly_display]],
        ),
    ]
    if account_count > 1:
        sheets.extend(
            [
                ("Account-Level Summary", monthly_display),
                ("Consolidated Summary", _display_frame(consolidated)),
            ]
        )
    diagnostic_breakdown = daily_hourly_classification_breakdown(interval_data) if interval_data is not None else pd.DataFrame()
    sheets.extend(
        [
            ("Daily Hourly Breakdown", diagnostic_breakdown),
            ("Input File Log", input_file_log.copy()),
            ("Estimation Notes", estimation_notes.copy()),
            ("Chart Data", monthly_display.copy()),
        ]
    )

    output = BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        workbook = writer.book
        for sheet_name, frame in sheets:
            if frame.empty and len(frame.columns) == 0:
                frame = pd.DataFrame({"Notes": ["No records for this report."]})
            frame.to_excel(writer, sheet_name=sheet_name, index=False)
            worksheet = writer.sheets[sheet_name]
            if sheet_name == "Official Dashboard":
                _format_official_dashboard_sheet(workbook, worksheet, frame)
            else:
                _format_standard_sheet(workbook, worksheet, frame, sheet_name)
    return output.getvalue()
