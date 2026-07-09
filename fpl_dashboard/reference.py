"""Project-specific reference comparison diagnostics.

These values come from the known approved dashboard screenshot used while
validating the ITAC FPL Dashboard app. They are intentionally isolated here so
normal processing does not hardcode or tune report output to the reference.
"""

from __future__ import annotations

import pandas as pd


REFERENCE_ROWS = [
    (2024, 9, 18872, 211281, 230153, 69723, 141558, 3072, 15800),
    (2024, 10, 19356, 228804, 248160, 75505, 153299, 3151, 16205),
    (2024, 11, 18479, 206881, 225360, 68271, 138610, 3008, 15471),
    (2024, 12, 15408, 190032, 205440, 62711, 127321, 2508, 12900),
    (2025, 1, 16046, 170543, 186589, 56279, 114264, 2612, 13434),
    (2025, 2, 14058, 173382, 187440, 57216, 116166, 2289, 11769),
    (2025, 3, 17095, 169475, 186570, 43866, 125609, 2783, 14312),
    (2025, 4, 12735, 176751, 189486, 63726, 113025, 1814, 10921),
    (2025, 5, 16402, 198568, 214970, 65265, 133303, 2538, 13864),
    (2025, 6, 17160, 199088, 216248, 66712, 132376, 1847, 15313),
    (2025, 7, 13674, 196274, 209948, 67471, 128803, 1340, 12334),
    (2025, 8, 12007, 146422, 158429, 50801, 95621, 1966, 10041),
]

REFERENCE_COLUMNS = [
    "Year",
    "Month",
    "Not Operating",
    "Operating Shift",
    "Total kWh",
    "On Peak Operating",
    "Off Peak Operating",
    "On Peak Not Operating",
    "Off Peak Not Operating",
]

APP_COLUMN_MAP = {
    "Not Operating": "Non-Operating kWh",
    "Operating Shift": "Operating kWh",
    "Total kWh": "Total kWh",
    "On Peak Operating": "On-Peak Operating kWh",
    "Off Peak Operating": "Off-Peak Operating kWh",
    "On Peak Not Operating": "On-Peak Non-Operating kWh",
    "Off Peak Not Operating": "Off-Peak Non-Operating kWh",
}


def reference_table() -> pd.DataFrame:
    return pd.DataFrame(REFERENCE_ROWS, columns=REFERENCE_COLUMNS)


def reference_comparison(monthly_summary: pd.DataFrame) -> pd.DataFrame:
    """Compare app monthly summary output against the known validation reference.

    This diagnostic is intentionally project-specific and optional. It does not
    alter app calculations.
    """
    if monthly_summary.empty:
        return pd.DataFrame()

    reference = reference_table()
    app = monthly_summary.copy()
    rows: list[dict[str, object]] = []
    for _, ref_row in reference.iterrows():
        year = int(ref_row["Year"])
        month = int(ref_row["Month"])
        month_label = pd.Timestamp(year, month, 1).strftime("%B %Y")
        match = app[(app["Year"].astype(int) == year) & (app["Month"].astype(int) == month)]
        if match.empty:
            for metric in APP_COLUMN_MAP:
                rows.append(
                    {
                        "Month": month_label,
                        "Metric": metric,
                        "Reference Value": float(ref_row[metric]),
                        "App Value": pd.NA,
                        "Difference": pd.NA,
                        "Percent Difference": pd.NA,
                        "Data Source": "Missing from app output",
                    }
                )
            continue
        app_row = match.iloc[0]
        for metric, app_column in APP_COLUMN_MAP.items():
            reference_value = float(ref_row[metric])
            app_value = pd.to_numeric(pd.Series([app_row.get(app_column)]), errors="coerce").iloc[0]
            difference = app_value - reference_value if pd.notna(app_value) else pd.NA
            pct_difference = difference / reference_value * 100 if pd.notna(difference) and reference_value else pd.NA
            rows.append(
                {
                    "Month": month_label,
                    "Metric": metric,
                    "Reference Value": reference_value,
                    "App Value": app_value,
                    "Difference": difference,
                    "Percent Difference": pct_difference,
                    "Data Source": app_row.get("Data Source", "Unknown"),
                }
            )
    return pd.DataFrame(rows)
