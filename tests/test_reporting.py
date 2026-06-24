from io import BytesIO

import pandas as pd

from fpl_dashboard.processing import DEMAND_COLUMNS, ENERGY_COLUMNS
from fpl_dashboard.reporting import create_excel_report


def _monthly_summary():
    rows = []
    for account in ["A", "B"]:
        for month, source in [(1, "Actual"), (2, "Estimated")]:
            row = {
                "Account": account,
                "Year": 2025,
                "Month": month,
                "Data Source": source,
                "Estimate Method": "Actual uploaded interval file" if source == "Actual" else "Interpolated",
                "Confidence": "Normal",
                "Peak During Non-Operating": False,
                "Non-Operating %": 20.0,
            }
            for column in ENERGY_COLUMNS + DEMAND_COLUMNS:
                row[column] = 100.0
            rows.append(row)
    return pd.DataFrame(rows)


def test_excel_report_contains_required_sheets_with_estimates():
    file_log = pd.DataFrame({"Account": ["A"], "File name": ["jan.xlsx"], "Status": ["Valid"]})
    notes = pd.DataFrame({"Account number": ["A"], "Estimated month": ["February"]})
    content = create_excel_report(_monthly_summary(), file_log, notes)
    workbook = pd.ExcelFile(BytesIO(content))
    required = {
        "Monthly Summary",
        "Operating vs Non-Operating",
        "On-Peak vs Off-Peak Summary",
        "Demand Summary",
        "Account-Level Summary",
        "Consolidated Summary",
        "Input File Log",
        "Estimation Notes",
        "Chart Data",
    }
    assert required.issubset(set(workbook.sheet_names))
    monthly = pd.read_excel(BytesIO(content), sheet_name="Monthly Summary")
    assert "Estimated" in set(monthly["Data Source"])
