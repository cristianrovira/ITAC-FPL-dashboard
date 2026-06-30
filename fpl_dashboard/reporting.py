"""Excel report generation."""

from __future__ import annotations

from io import BytesIO

import pandas as pd

from .processing import DEMAND_COLUMNS, ENERGY_COLUMNS


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
        row["Confidence"] = "Very Low" if (group["Confidence"] == "Very Low").any() else ("Low" if (group["Confidence"] == "Low").any() else "Normal")
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


def create_excel_report(
    monthly_summary: pd.DataFrame,
    input_file_log: pd.DataFrame,
    estimation_notes: pd.DataFrame,
) -> bytes:
    """Create the complete report workbook in memory."""
    account_count = monthly_summary["Account"].nunique() if not monthly_summary.empty else 0
    consolidated = consolidated_summary(monthly_summary) if account_count > 1 else pd.DataFrame()
    monthly_display = _display_frame(monthly_summary)

    sheets: list[tuple[str, pd.DataFrame]] = [
        ("Monthly Summary", monthly_display),
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
    sheets.extend(
        [
            ("Input File Log", input_file_log.copy()),
            ("Estimation Notes", estimation_notes.copy()),
            ("Chart Data", monthly_display.copy()),
        ]
    )

    output = BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        workbook = writer.book
        header_format = workbook.add_format({"bold": True, "bg_color": "#F47321", "font_color": "#FFFFFF", "border": 1})
        estimated_format = workbook.add_format({"bg_color": "#FFF2CC"})
        for sheet_name, frame in sheets:
            if frame.empty and len(frame.columns) == 0:
                frame = pd.DataFrame({"Notes": ["No records for this report."]})
            frame.to_excel(writer, sheet_name=sheet_name, index=False)
            worksheet = writer.sheets[sheet_name]
            worksheet.freeze_panes(1, 0)
            worksheet.autofilter(0, 0, max(len(frame), 1), max(len(frame.columns) - 1, 0))
            for column_index, column in enumerate(frame.columns):
                worksheet.write(0, column_index, column, header_format)
                values = frame[column].astype(str) if not frame.empty else pd.Series(dtype=str)
                width = min(max(len(str(column)) + 2, int(values.str.len().max() if not values.empty else 0) + 2), 45)
                worksheet.set_column(column_index, column_index, width)
            if "Data Source" in frame.columns and len(frame):
                source_index = frame.columns.get_loc("Data Source")
                worksheet.conditional_format(
                    1,
                    0,
                    len(frame),
                    len(frame.columns) - 1,
                    {
                        "type": "formula",
                        "criteria": f'=${chr(65 + source_index)}2="Estimated"' if source_index < 26 else '=""',
                        "format": estimated_format,
                    },
                )
    return output.getvalue()
