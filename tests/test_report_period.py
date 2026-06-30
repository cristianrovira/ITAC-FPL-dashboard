import pandas as pd

from fpl_dashboard.extraction import ExtractedFile
from fpl_dashboard.report_period import account_report_windows, partial_period_warnings, suggested_report_end


def _file(period: str, rows: int = 2880, interval: float = 0.25):
    p = pd.Period(period, freq="M")
    return ExtractedFile(
        account="A",
        filename=f"{period}.xlsx",
        month=p.month,
        year=p.year,
        row_count=rows,
        detected_interval_hours=interval,
    )


def test_suggested_report_end_prefers_latest_complete_month():
    files = [_file("2026-03", rows=2976), _file("2026-04", rows=768)]
    assert suggested_report_end(files) == pd.Period("2026-03", freq="M")


def test_account_report_windows_can_use_selected_end_month():
    files = [_file("2025-10"), _file("2026-04", rows=768)]
    windows = account_report_windows(files, pd.Period("2026-02", freq="M"))
    assert list(windows["A"])[0] == pd.Period("2025-03", freq="M")
    assert list(windows["A"])[-1] == pd.Period("2026-02", freq="M")


def test_partial_period_warning_flags_short_file():
    warnings = partial_period_warnings([_file("2026-04", rows=768)])
    assert warnings
    assert "April 2026" in warnings[0]
