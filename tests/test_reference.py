import pandas as pd

from fpl_dashboard.reference import reference_comparison, reference_table


def test_reference_table_contains_known_annual_total():
    reference = reference_table()
    assert reference["Total kWh"].sum() == 2458793
    assert reference["Not Operating"].sum() == 191292


def test_reference_comparison_reports_month_metric_difference():
    summary = pd.DataFrame(
        [
            {
                "Year": 2024,
                "Month": 9,
                "Data Source": "Actual",
                "Non-Operating kWh": 18800,
                "Operating kWh": 211000,
                "Total kWh": 229800,
                "On-Peak Operating kWh": 69000,
                "Off-Peak Operating kWh": 142000,
                "On-Peak Non-Operating kWh": 3000,
                "Off-Peak Non-Operating kWh": 15800,
            }
        ]
    )
    comparison = reference_comparison(summary)
    total = comparison[(comparison["Month"] == "September 2024") & (comparison["Metric"] == "Total kWh")].iloc[0]
    assert total["Reference Value"] == 230153
    assert total["App Value"] == 229800
    assert total["Difference"] == -353
