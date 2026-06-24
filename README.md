# ITAC FPL Dashboard Analysis Tool

This repository contains the University of Miami Industrial Assessment Center's standalone FPL interval-data dashboard. It validates monthly Excel uploads, classifies readings by operating schedule and legacy on/off-peak windows, calculates monthly energy and demand summaries, estimates missing monthly summaries with explicit labels, displays dashboard charts, and creates an Excel report.

This app is separate from the ITAC Bill Analysis Tool. The original prototype is retained unchanged at `legacy/fpl_original.py` for reference.

## Run in GitHub Codespaces

1. Open the repository in a Codespace.
2. In the terminal, create and activate a virtual environment if desired.
3. Install the dependencies:

   ```bash
   python -m pip install -r requirements.txt
   ```

4. Start the app:

   ```bash
   streamlit run app.py
   ```

5. Open the forwarded Streamlit port when Codespaces prompts you.

## App workflow

### 1. Upload files

Choose the number of FPL accounts and upload one or more `.xlsx` or `.xls` interval-data files for each account. Accounts are labeled automatically as `Account 1`, `Account 2`, and so on. Files are held in memory for the current Streamlit session; the app does not permanently store client workbooks.

The reader prioritizes the legacy FPL layout with headers on Excel row 4, then checks several common header positions. It accepts a combined timestamp column such as `DateTime` or `Timestamp`, or separate `Date` and `Time` columns.

### 2. Define operating days and shifts

Choose one of these presets:

- Standard business hours: Monday–Friday, 8:00 AM–5:00 PM
- Two shifts
- Three shifts
- 24/7 operation
- Custom schedule with up to three shifts

The Configured Shifts table is editable. Changing a preset shift time or its operating days automatically switches the preset to Custom schedule while preserving the edited values. Custom schedules can contain up to three shifts. Overnight shifts such as 11:00 PM–6:30 AM are supported. After-midnight readings belong to the day on which the overnight shift started.

### 3. Confirm detected data

Before processing, the app displays an Input File Log preview containing the account, file name, assigned reporting month and year, timestamp and demand columns, interval, row count, status, and messages. A normal month-length billing period may begin in one calendar month and end in the next; it is assigned to one reporting month and is not treated as a duplicate. The app blocks unreadable workbooks, missing required fields, and duplicate assigned reporting months, and flags short files, uncertain timestamp spacing, inconsistent intervals, or files spanning more than two calendar months or 45 days.

Demand columns are detected from numeric columns whose names suggest demand or kW while excluding kWh/energy columns. When detection is missing or ambiguous, the app asks the user to choose. Selecting multiple demand columns adds their row-level kW values into one account total.

## Data interval

The data interval is the amount of time represented by each row. Demand readings are kW; monthly energy is calculated as:

```text
interval kWh = demand kW × interval hours
```

The interface uses intuitive labels:

| Label | Internal hours |
|---|---:|
| 15 minutes | 0.25 |
| 30 minutes | 0.5 |
| 1 hour | 1.0 |

The app detects the interval from the median positive spacing between timestamps and measures how consistently the file follows that spacing. Use **Manually override detected interval** when the source file is irregular or the detected value is not appropriate. A wrong interval changes calculated kWh but does not scale peak kW.

## Classification and summaries

An interval is Operating when it falls on a selected operating day and within any active shift. All other readings are Non-Operating. Shift end times are exclusive, preventing adjacent shifts from double-counting their boundary.

The app preserves the legacy on-peak rule:

- Saturday and Sunday: off-peak
- April–October weekdays: hours 12:00 PM through 9:59 PM
- November–March weekdays: hours 6:00 AM through 10:59 AM and 6:00 PM through 10:59 PM

This rule came from the legacy prototype and is **not represented as a verified current FPL tariff**. Confirm it against the facility's applicable rate schedule before using it for tariff-sensitive decisions. The rule is isolated in `fpl_dashboard/classification.py` so future students can update it safely.

Monthly output includes total kWh, peak demand, operating/non-operating energy and demand, on/off-peak energy and demand, weekend energy, overnight energy, source labels, method, and confidence.

## Missing-month estimation

Missing months are never silently estimated. The app lists them and requires this confirmation:

> I understand that missing months will be estimated using nearby available months.

Actual interval files are processed first. Every workbook keeps all of its interval rows but contributes to its single assigned reporting month. The annual view is the 12 consecutive reporting months ending with the latest uploaded month, so the window can cross from one calendar year into the next. Only monthly summary values are estimated—no fake interval-level data is generated.

- One missing month between actual months uses their midpoint.
- Consecutive missing months use linear interpolation across the gap.
- January and December can use circular December/February or November/January neighbors.
- If only one actual month exists, its monthly values are carried to the other months with Low confidence.

Every row includes `Data Source`, `Estimate Method`, and `Confidence`. Estimated values remain labeled in Streamlit, chart tooltips/source data, Monthly Summary, and Estimation Notes.

## Excel report

The download includes:

1. Monthly Summary
2. Operating vs Non-Operating (Excel shortens this name to stay within its 31-character sheet-name limit)
3. On-Peak vs Off-Peak Summary
4. Demand Summary
5. Account-Level Summary when multiple accounts are present
6. Consolidated Summary when multiple accounts are present
7. Input File Log
8. Estimation Notes
9. Chart Data

Estimated rows are highlighted in the workbook. Energy and demand display values are rounded to whole kWh/kW, while percentages use one decimal place.

## Run tests

```bash
python -m pytest
```

The tests cover 15/30/60-minute interval detection, cross-calendar-month billing periods, rolling 12-month windows, duplicate reporting months, ordinary and overnight shifts, the documented legacy peak rule, cross-year interpolation, source labels, and Excel report sheet creation.

## Deploy to Streamlit Community Cloud

1. Push this repository to GitHub.
2. In Streamlit Community Cloud, create an app from the repository.
3. Select the `main` branch and set the entry point to `app.py`.
4. Deploy. No secrets or local file paths are required.

## Project structure

```text
app.py                         Streamlit interface
fpl_dashboard/extraction.py    Excel reading and automatic detection
fpl_dashboard/validation.py    File log and collection validation
fpl_dashboard/processing.py    Interval normalization and monthly aggregation
fpl_dashboard/classification.py Schedule and peak classification
fpl_dashboard/estimation.py    Summary-only missing-month estimation
fpl_dashboard/reporting.py     In-memory Excel workbook generation
fpl_dashboard/charts.py        Altair dashboard charts
fpl_dashboard/utils.py         Shared constants and helpers
tests/                         Automated tests
legacy/fpl_original.py         Unmodified legacy reference
```

## Known limitations

- Source workbooks must expose recognizable timestamps within the checked header rows. Unusual FPL exports may require a new extraction adapter.
- Each uploaded file should represent one monthly reporting period. Normal periods spanning two adjacent calendar months are supported and assigned to the dominant reporting month; files longer than 45 days or spanning more than two calendar months are flagged for review.
- Each account uses one rolling 12-month window ending at its latest assigned reporting month. Uploads older than that window are not included in the final annual view.
- For the consolidated report, energy is additive. Monthly account demand peaks are summed conservatively because estimated months do not contain coincident interval readings; this may exceed the true coincident facility peak.
- Interpolation is a screening estimate and does not model weather, production, holidays, billing cycles, or seasonal rate changes.
- Timestamps are treated as local, timezone-naive values. Daylight-saving anomalies should be reviewed in the Input File Log.
- Potential Issues Detected uses simple thresholds and is not a substitute for engineering review.

## Maintenance notes

- Keep client Excel files out of Git. The `.gitignore` excludes common workbook formats.
- Add new workbook layouts in `extraction.py`; avoid format-specific parsing in `app.py`.
- Update tariff logic only in `classification.py`, with a source citation and corresponding tests.
- Add summary fields in `processing.py`, then include them in `estimation.py`, `reporting.py`, and tests so actual and estimated outputs stay aligned.
- Preserve `legacy/fpl_original.py` as historical reference.
