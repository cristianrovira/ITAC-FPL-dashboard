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

### 2. Define operating shifts

Choose one of these schedule modes:

- Standard business hours: Monday–Friday, 8:00 AM–5:00 PM
- Two shifts
- Three shifts
- 24/7 operation
- Continuous operating window
- Custom schedule with up to three shifts

Fixed weekly shifts and custom day-by-day schedules use the editable Configured Shifts table. Each row has its own Days value, so weekday and weekend shifts can use different operating days. Examples include Mon-Fri, Sat-Sun, weekdays, weekends, and 24/7. Changing a preset shift time or days automatically switches the mode to Custom day-by-day schedule while preserving the edited values. Custom day-by-day schedules can contain up to three shifts. Overnight shifts such as 11:00 PM-6:30 AM are supported. A shift ending at 12:00 AM runs until midnight at the end of the selected day; after-midnight readings for overnight shifts belong to the day on which the overnight shift started. Continuous operating window lets the user choose one weekly start day/time and one weekly end day/time, such as Sunday 12:00 PM through Friday 7:00 PM or Friday 10:00 PM through Monday 6:00 AM. The start boundary is inclusive and the end boundary is exclusive. Internally, the app converts timestamps and the configured endpoints to minutes from the start of the week so windows that wrap across the end of the week are handled cleanly.

### 3. Confirm detected data

Before processing, the app displays an Input File Log preview containing the account, file name, assigned reporting month and year, timestamp and demand columns, interval, row count, status, and messages. A normal month-length billing period may begin in one calendar month and end in the next; it is assigned to one reporting month and is not treated as a duplicate. The app blocks unreadable workbooks, missing required fields, and duplicate assigned reporting months, and flags short files, uncertain timestamp spacing, inconsistent intervals, partial reporting months, or files spanning more than two calendar months or 45 days.

The app also suggests a 12-month report period from the uploaded files, preferring the latest reasonably complete reporting month when the newest upload appears partial. Most users can accept this detected period. If the official reporting year is different, check **Change detected report period** and choose the report end month; the dashboard will cover the 12 months ending with that month.

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

### Schedule mode guidance

- **Fixed weekly shifts**: recurring weekly rows such as Mon-Fri 8:00 AM-5:00 PM or multiple weekday shifts.
- **Continuous operating window**: one uninterrupted weekly window selected by start day/time and end day/time. This is best for facilities that run continuously across several days but shut down for part of the week.
- **24/7 operation**: every interval is operating.
- **Custom day-by-day schedule**: editable schedule rows for site-specific operating patterns.

## Classification and summaries

An interval is Operating when it falls on a selected operating day and within any active shift. All other readings are Non-Operating. Shift end times are exclusive, preventing adjacent shifts from double-counting their boundary. The app also offers continuous-facility idle-load and hybrid classification modes for facilities whose operating state is better represented by demand level than by a strict time clock.

The app supports timestamp-alignment assumptions for classification. The default treats timestamps as the start of the interval. Advanced settings can treat timestamps as either interval-start or interval-ending labels.

The default on-peak rule uses exact legacy-style time windows:

- Saturday and Sunday: off-peak
- April-October weekdays: 12:00 PM up to, but not including, 9:00 PM
- November-March weekdays: 6:00 AM up to, but not including, 10:00 AM, and 6:00 PM up to, but not including, 10:00 PM

A legacy whole-hour option is also available for comparison with older prototype behavior. These rules are **not represented as verified current FPL tariff rules**. Confirm them against the facility's applicable rate schedule before using the output for tariff-sensitive decisions. The rule is isolated in `fpl_dashboard/classification.py` so future students can update it safely.

Monthly output includes total kWh, peak demand, operating/non-operating energy and demand, on/off-peak energy and demand, weekend energy, overnight energy, source labels, method, and confidence.

## Missing-month estimation

Missing months are never silently estimated. The app lists them and requires this confirmation:

> I understand that missing months will be estimated from available month trends.

Actual interval files are processed first. Every workbook keeps all of its interval rows but contributes to its single assigned reporting month. The annual view is the selected 12-month report period, so the window can cross from one calendar year into the next. Only monthly summary values are estimated; no fake interval-level data is generated.

- One missing month between actual months uses their midpoint.
- Consecutive missing months between actual months use linear interpolation across the gap.
- Missing months before the first actual month or after the last actual month use a capped trend extrapolation from the nearest two actual months instead of flat-copying one month repeatedly.
- If only one actual month exists, its monthly values are carried to other months, with far-away months marked Very Low confidence.
- Long leading or trailing estimate runs are marked Low or Very Low confidence because they are inherently less reliable.

Every row includes `Data Source`, `Estimate Method`, and `Confidence`. Estimated values remain labeled in Streamlit, chart tooltips/source data, Monthly Summary, and Estimation Notes.

## Excel report

The download includes:

1. Monthly Summary
2. Operating vs Non-Operating (Excel shortens this name to stay within its 31-character sheet-name limit)
3. On-Peak vs Off-Peak Summary
4. Demand Summary
5. Account-Level Summary when multiple accounts are present
6. Consolidated Summary when multiple accounts are present
7. Classification Audit
8. Daily Hourly Breakdown
9. Input File Log
10. Estimation Notes
11. Chart Data

Estimated rows are highlighted in the workbook. Energy and demand display values are rounded to whole kWh/kW, while percentages use one decimal place.

For validation of the known approved screenshot only, the Streamlit app can optionally include a project-specific `Reference Comparison` diagnostic sheet. This comparison is isolated in `fpl_dashboard/reference.py` and does not change calculations or tune report output.

## Run tests

```bash
python -m pytest
```

The tests cover 15/30/60-minute interval detection, cross-calendar-month billing periods, detected and selected report windows, duplicate reporting months, partial-month warnings, ordinary/overnight/24-7 shifts, the Sunday noon-Friday 7 PM preset, timestamp alignment, idle-load classification, the documented peak rules, cross-year interpolation, trend extrapolation, source labels, and Excel report sheet creation.

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
