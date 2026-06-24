"""Altair charts used by the Streamlit dashboard."""

from __future__ import annotations

import altair as alt
import pandas as pd


def prepare_chart_data(summary: pd.DataFrame) -> pd.DataFrame:
    frame = summary.copy()
    frame["Month / Year"] = pd.to_datetime(
        dict(year=frame["Year"], month=frame["Month"], day=1)
    ).dt.strftime("%b %Y")
    frame["Month Sort"] = frame["Year"] * 100 + frame["Month"]
    return frame


def _single_metric(frame: pd.DataFrame, metric: str, title: str, y_title: str) -> alt.Chart:
    encoding: dict[str, object] = {
        "x": alt.X("Month / Year:N", sort=alt.SortField("Month Sort"), title="Month"),
        "y": alt.Y(f"{metric}:Q", title=y_title),
        "color": alt.Color(
            "Data Source:N",
            scale=alt.Scale(domain=["Actual", "Estimated"], range=["#005030", "#F47321"]),
        ),
        "tooltip": [
            "Account:N",
            "Month / Year:N",
            alt.Tooltip(f"{metric}:Q", format=",.0f"),
            "Data Source:N",
            "Estimate Method:N",
        ],
    }
    if frame["Account"].nunique() > 1:
        encoding["row"] = alt.Row("Account:N", title=None)
    return alt.Chart(frame).mark_bar().encode(**encoding).properties(title=title, height=300)


def _comparison(frame: pd.DataFrame, columns: list[str], labels: list[str], title: str, y_title: str) -> alt.Chart:
    melted = frame.melt(
        id_vars=["Account", "Month / Year", "Month Sort", "Data Source", "Estimate Method"],
        value_vars=columns,
        var_name="Category",
        value_name="Value",
    )
    melted["Category"] = melted["Category"].map(dict(zip(columns, labels)))
    encoding: dict[str, object] = {
        "x": alt.X("Month / Year:N", sort=alt.SortField("Month Sort"), title="Month"),
        "xOffset": alt.XOffset("Category:N"),
        "y": alt.Y("Value:Q", title=y_title),
        "color": alt.Color("Category:N"),
        "opacity": alt.Opacity(
            "Data Source:N",
            scale=alt.Scale(domain=["Actual", "Estimated"], range=[1.0, 0.55]),
        ),
        "tooltip": [
            "Account:N",
            "Month / Year:N",
            "Category:N",
            alt.Tooltip("Value:Q", format=",.0f"),
            "Data Source:N",
            "Estimate Method:N",
        ],
    }
    if frame["Account"].nunique() > 1:
        encoding["row"] = alt.Row("Account:N", title=None)
    return alt.Chart(melted).mark_bar().encode(**encoding).properties(title=title, height=300)


def dashboard_charts(summary: pd.DataFrame) -> list[tuple[str, alt.Chart, str]]:
    frame = prepare_chart_data(summary)
    return [
        (
            "Monthly total kWh",
            _single_metric(frame, "Total kWh", "Monthly Total Energy", "Energy (kWh)"),
            "This chart shows total interval-derived energy by month. Orange bars are estimated monthly summaries.",
        ),
        (
            "Monthly peak demand",
            _single_metric(frame, "Peak Demand kW", "Monthly Peak Demand", "Demand (kW)"),
            "This chart shows each month's highest interval demand. Estimated peaks are interpolated summary values, not fabricated readings.",
        ),
        (
            "Operating vs non-operating kWh",
            _comparison(frame, ["Operating kWh", "Non-Operating kWh"], ["Operating", "Non-operating"], "Operating vs Non-Operating Energy", "Energy (kWh)"),
            "High non-operating energy may indicate equipment running outside the configured production schedule.",
        ),
        (
            "Operating vs non-operating demand",
            _comparison(frame, ["Operating Demand kW", "Non-Operating Demand kW"], ["Operating", "Non-operating"], "Operating vs Non-Operating Demand", "Demand (kW)"),
            "This chart compares monthly peak demand inside and outside the configured operating schedule.",
        ),
        (
            "On-peak vs off-peak kWh",
            _comparison(frame, ["On-Peak kWh", "Off-Peak kWh"], ["On-peak", "Off-peak"], "On-Peak vs Off-Peak Energy", "Energy (kWh)"),
            "This chart applies the documented legacy time-of-use classification; verify it against the site's applicable tariff.",
        ),
        (
            "On-peak vs off-peak demand",
            _comparison(frame, ["On-Peak Demand kW", "Off-Peak Demand kW"], ["On-peak", "Off-peak"], "On-Peak vs Off-Peak Demand", "Demand (kW)"),
            "This chart separates monthly demand peaks by the legacy on-peak and off-peak windows.",
        ),
    ]
