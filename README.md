# Retail Performance & Planning Lab

[Open Live Demo](https://retail-sales-performance-analysis-w7etlbcp6rojbyqueaumuc.streamlit.app/)

A retail analytics portfolio project for Analyst job applications. Phase 1 establishes a reproducible data-quality and measurement foundation using the Superstore sample workbook. Phase 2 adds a Streamlit and Plotly dashboard. Phase 3 adds monthly Performance Drivers comparisons. Phase 4 adds simplified Scenario Planning sensitivity analysis using the current filtered Sales and Profit. The audience is a Finance Manager. The repository is prepared for Streamlit Community Cloud; no hosted deployment has been performed or verified.

## Environment and Setup

Recommended Python version: **3.14**, matching the clean Windows validation environment (3.14.6). Select Python 3.14 explicitly in Community Cloud Advanced settings. Linux and the hosted platform remain unverified; a successful local check is not a hosted deployment result.

`requirements.txt` contains only five direct runtime dependencies: Streamlit 1.66.1, pandas 3.0.5, Plotly 7.1.0, xlrd 2.0.2, and openpyxl 3.1.5. These are the application's previously validated versions. Transitive dependencies are resolved by the installer rather than copied from a Windows `pip freeze`. No Windows-only packages are explicitly required. Both Excel engines are included because the loader supports the supplied legacy binary workbook and genuine XLSX files.

From the repository root in PowerShell:

```powershell
# Only create the environment if .venv does not already exist.
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
```

Use the explicit interpreter path to avoid accidentally running global Python. A local virtual environment is recommended, but neither the app nor the analysis scripts require a particular environment name or interpreter location. If the Windows `py` launcher is unavailable, use the installed Python 3.14 executable to create the environment instead.

Optional notebook development dependencies are managed separately:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

Select the project `.venv` kernel in the notebook. Community Cloud installs the root `requirements.txt`, not this optional development file. Tests use standard-library `unittest` and Streamlit's included AppTest; notebook dependencies are not needed for tests. Streamlit's PyArrow dependency can affect pandas string inference; the shared date parser explicitly maintains boolean masks for empty selections.

## Run the Analysis

```powershell
.\.venv\Scripts\python.exe scripts\run_analysis.py
```

The default inspection reference date is `2026-11-01`, matching the original overview. It flags later sample dates but **does not filter records**. To set it explicitly:

```powershell
.\.venv\Scripts\python.exe scripts\run_analysis.py --as-of-date 2026-11-01
```

Default paths are resolved relative to the repository, so an absolute script path also works from another working directory. `--output-dir <path>` selects a separate output directory. Writing into `data/raw/` is blocked. Repeated runs overwrite the named generated artifacts, preserve all source records, and produce identical analytical tables for the same input and reference date. Only run metadata, such as execution time and interpreter filename, may change.

Run the behavioral checks:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Repository Structure

```text
data/raw/sample_-_superstore.xlsx   Original workbook, never overwritten
retail_lab/analysis.py             Shared loading, profiling, metrics, and validation
retail_lab/dashboard.py            One filtered model, Plotly figures, and CSV payloads
retail_lab/drivers.py              Period comparisons, contributions, and summaries
retail_lab/scenario.py             Sensitivity calculations, definitions, and exports
retail_lab/sql_analysis.py         DuckDB detail registration and pandas reconciliation
scripts/run_analysis.py            Command-line entry point
scripts/run_drivers.py             Reproducible comparison export without a UI
scripts/run_sql_analysis.py        Optional SQL analysis CLI
notebooks/01_data_overview.ipynb    Executed overview using shared analysis code
tests/test_analysis.py             Financial definitions and edge-case checks
tests/test_dashboard.py            Filter, export, chart, and Streamlit widget checks
tests/test_drivers.py              Period-change and contribution validation
tests/test_scenario.py             Explicit scenario examples and UI consistency
outputs/                          Generated summaries, metrics, and audit metadata
sql/                              Executable selection, KPI, window, and safe-join queries
dashboard/app.py                  Local Streamlit dashboard entry point
dashboard/drivers_view.py          Performance Drivers tab presentation
dashboard/scenario_view.py         Scenario Planning tab presentation
.streamlit/config.toml             Theme and usage-statistics preference
requirements.txt                   Direct runtime dependencies for local/cloud use
requirements-dev.txt               Optional notebook tools
requirements-sql.txt               Optional DuckDB analysis dependency
tests/test_deployment.py            Real entry points from another working directory
images/                           Reserved for later analysis figures
```

## Source Format and Worksheet Relationships

The source filename ends in `.xlsx`, but its signature identifies a legacy Excel binary workbook. The loader inspects the signature and selects `xlrd`; genuine XLSX ZIP containers use `openpyxl`. The original file is not renamed or converted.

| Worksheet | Observed rows | Fields | Grain and relationship |
| --- | ---: | ---: | --- |
| Orders | 10,194 | 21 | One product sales line item within an order. `Row ID` is unique in this file. `Order ID` can repeat. |
| People | 4 | 2 | One regional manager mapping per `Region`; join from `Orders.Region`. |
| Returns | 296 | 2 | One return flag per `Order ID`; join from `Orders.Order ID`. No product-level return quantity or amount is available. |

**All metrics are computed from Orders alone.** Relationship checks perform trial left joins with `validate="many_to_one"`, verify unchanged line counts and measure totals, and block joins with duplicate or missing right-side keys. No deduplication is used to make a join pass. An unmatched order in Returns is expected because the sheet is a return list; it does not prove that the list is complete. People and Returns are not additional sales fact tables.

## Metric Definitions

| Metric | Definition | Missing or zero handling |
| --- | --- | --- |
| Sales | Sum of finite numeric `Orders.Sales` values | Unknown amounts are not zero. Excluded missing or invalid inputs make the sum `partial`; no usable values yields null. |
| Profit | Sum of finite numeric `Orders.Profit` values | Absent field yields null with `profit_status=field_missing`. Missing or invalid inputs follow the Sales policy. |
| Profit Margin | Total Profit / Total Sales | Stored as a fraction. Null if Sales is zero, Profit is absent, no usable measure exists, or either measure is incomplete. Never average row or group margins. |
| Order Count | Distinct nonmissing `Order ID` count | Null and whitespace-only IDs are excluded and counted separately. Other identifiers are preserved without case or whitespace normalization. |
| Order Line Count | Number of source Orders data rows | Includes every row, even if an identifier, date, or measure is anomalous. |

No currency is assumed. Amounts use source units and full source precision. Display rounding is separate from computation.

## Data-Quality Checks and Handling

Every worksheet is inspected for field types, row count, declared grain, missing values, whitespace-only strings, distinct values, date fields, and exact duplicates. Date-named columns are parsed with invalid values explicitly counted. Typed Excel dates are decoded by the reader; untyped numeric date values are flagged as invalid rather than interpreted as nanoseconds since 1970.

Duplicate categories are reported separately in `duplicate_summary.csv`:

- `full_row`: all source fields, including identifiers, match.
- `order_id`: repeated order IDs. Expected in Orders at line-item grain; a concern in Returns, whose grain is one row per order.
- `order_line_id`: repeated `Row ID`, the observed source line identifier.
- `content_excluding_row_id`: matching content after excluding the row identifier, retained for review.
- `order_product`: repeated `Order ID + Product ID`; not assumed to be a unique key.
- `region_key`: repeated People regions, which would make a many-to-one join unsafe.

Each category reports extra rows beyond the first occurrence, all affected rows, repeated key groups, and missing key rows. `duplicate_examples.csv` contains at most 10 affected rows per worksheet and category with Excel row references, not an exhaustive duplicate export. Key-based duplicate checks exclude missing keys and report their missing counts separately. Full-row duplicate checks include all records.

Additional checks flag invalid or non-finite numbers, negative Sales and Profit, zero Sales, nonpositive Quantity, Discount outside `[0, 1]`, shipment before order, dates after the inspection reference date, and Sales/Profit outliers outside 1.5-IQR bounds. IQR is a review heuristic, not an automatic business-validity rule. **No anomalous rows are deleted, deduplicated, clipped, or imputed.** Negative profit is retained because it may represent a genuine loss.

## Dimensional Summaries and Reconciliation

Summaries are produced separately by order month (`YYYY-MM` from `Order Date`), sales region (`Region`), and product category (`Category`). Each includes the five base metrics and measure-availability counts/statuses.

- Group with `dropna=False`. Null or whitespace-only grouping values and missing/invalid order dates stay in an `__UNASSIGNED__` output group. Their valid amounts and line counts remain included, so invalid dates cannot silently remove sales from the monthly reconciliation.
- The unassigned label is applied only to the grouping column on export. Missing numeric outputs remain blank in CSV and null in JSON.
- Reconcile Sales, Profit, and Order Line Count across **all** groups to the grand totals. Use absolute tolerance `1e-6` and relative tolerance `1e-12` for floating-point amounts. Values are not rounded to cents before summation.
- Order Count is not additive when an order spans multiple categories or other groups. Verify the distinct union across groups instead; the sum of group order counts is informational only.
- Profit Margin is not additive. Each group divides its total Profit by its total Sales under the same availability rules.
- Missing/invalid measures can still yield reconciled **partial** sums. Reconciliation proves coverage and arithmetic consistency, not that unknown amounts are complete or unusual records are correct.

Failed reconciliation or trial-join preservation checks produce a nonzero exit status after saving diagnostic outputs. Unsafe dimension keys block only trial enrichment; base metrics continue to use Orders without a join. Unavailable Profit is reported as unavailable rather than a passing zero.

## Generated Outputs

| File under `outputs/` | Purpose |
| --- | --- |
| `metrics.json`, `metrics.csv` | Grand totals and availability statuses |
| `sheet_summary.csv` | Worksheet dimensions, grain, missingness, and exact duplicates |
| `field_profile.csv` | Every field's type, meaning, missingness, date range, and invalid value counts |
| `quality_checks.csv` | Check counts, row denominators, rates, and handling notes |
| `duplicate_summary.csv`, `duplicate_examples.csv` | Separate duplicate definitions and bounded traceable examples |
| `relationships.csv` | Join cardinality, unmatched keys, and row/amount preservation |
| `monthly_summary.csv`, `regional_summary.csv`, `category_summary.csv` | Dimensional metrics retaining unassigned rows |
| `reconciliation.csv` | Expected totals, observed totals, differences, and statuses |
| `data_quality_summary.md` | Readable findings, metric definitions, and interpretation limits |
| `run_manifest.json` | Source hash before/after, engine, environment, reference date, and artifact list |

## Observed Phase 1 Results

For the preserved workbook with inspection reference date `2026-11-01`:

| Metric | Value |
| --- | ---: |
| Sales | 2,326,534.3543 |
| Profit | 292,296.8146 |
| Profit Margin | 12.563615% |
| Order Count | 5,111 |
| Order Line Count | 10,194 |

There are 48 observed order months, 4 regions, and 3 categories. All 12 additive-measure and distinct-order-union reconciliation checks pass. The 3 group-order-count sums are informational, not additional passing reconciliations.

## Known Data Limitations

- This is a sample dataset, not verified realized operating performance. Order dates cover `2023-01-03` through `2026-12-30`; shipment dates extend to `2027-01-05`. There are 938 order lines dated after the inspection reference date. No as-of reporting filter is applied.
- No missing values, blank strings, exact full-row duplicates, or duplicate Row IDs were detected. Excluding Row ID identifies 2 additional duplicate records involving 4 rows. The order/product combination has 11 additional repeated-key rows. These records remain in totals pending source review.
- There are 1,901 negative-profit lines, retained for profitability analysis. Outliers are flagged without removal.
- The sample covers the United States and Canada. Currency comparability, tax treatment, and profit cost basis are unconfirmed. Do not label combined amounts as verified USD revenue.
- Postal Code contains numeric US values and Canadian text values. Treat it as an identifier; the source may not preserve all original leading zeros or full postal-code detail.
- Returns has no product ID, quantity, amount, or return date. It cannot support net-sales adjustments, returned-unit counts, or return timing. Its completeness is unverified.
- People has no effective dates, so historical manager assignments cannot be established.
- `Row ID` is an observed unique identifier, not a guaranteed cross-refresh key. Successful reconciliation does not establish business accuracy.

Raw workbook SHA-256: `5045ed4fec49cae2cc0dfc5ab0d204ba09efa767fe00c996822499e8cc42fbbd`.

## Phase 2: Local Dashboard

From the repository root, generate or refresh Phase 1 outputs, then start the app:

```powershell
.\.venv\Scripts\python.exe scripts\run_analysis.py
.\.venv\Scripts\python.exe -m streamlit run dashboard\app.py --server.address 127.0.0.1 --server.port 8501
```

Open [http://127.0.0.1:8501](http://127.0.0.1:8501). Stop the foreground server with `Ctrl+C`. The command above binds local runs to loopback. The shared configuration selects a light theme and disables usage statistics; it does not fix the server address or port, so a hosting platform can manage them.

### Dashboard Views

- Four KPI cards: Sales, Profit, Profit Margin, and Order Count, with expandable definitions and source-unit disclosure.
- Inclusive order-date range, region, and product-category filters; a reset button restores full coverage. All listed regions/categories are selected initially. Clearing a multiselect returns an empty selection rather than silently selecting everything.
- Separate monthly Sales and Profit charts, separate regional Sales and Profit comparisons, and a horizontal Sub-Category profit ranking. Loss-making subcategories have both a contrasting color and a `(loss)` label. Amount axes and hover labels use source units, not assumed USD.
- A warning identifies first/last months truncated by the selected dates. The initial source coverage itself starts on January 3 and ends on December 30, so the initial view also flags partial calendar-month coverage. No full-month forecast or extrapolation is implied.
- Missing calendar months appear as gaps in trend lines, not invented zero observations. Missing/invalid dates are included by default when present, remain in metrics and unassigned monthly exports, and are explicitly disclosed as absent from the time axis. The checkbox can exclude them.
- The Data quality tab shows flags for the **current selection**. Its expandable Phase 1 source-wide audit is explicitly fixed to the full workbook, independent of active filters. Selected-scope IQR bounds are recalculated rather than copied from the full source.
- The Details & exports tab shows all selected source lines and provides four CSV downloads: order lines, monthly summary, regional summary, and category summary. A fifth download provides selection context and unrounded metrics as JSON. Postal Code is displayed as text for Arrow compatibility; source values and detail exports are preserved.

### One Selection and Shared Definitions

`retail_lab.dashboard.filter_orders()` applies all filters once to a derived order view. `build_dashboard_model()` then calls the existing `calculate_metrics()`, `aggregate_dimension()`, `reconcile_dimension()`, and `profile_workbook()` functions. KPI cards, all five Plotly figures, the displayed detail table, selected-scope quality checks, and download payloads consume that same model. The Streamlit entry point contains presentation and widget logic, not a second financial calculation implementation.

Category filters select matching line items, not complete baskets for the matching orders. Order Count therefore counts distinct orders containing selected line items. Group order counts remain nonadditive.

On source load, `load_verified_source()` checks the raw-source hash, recalculates full metrics and reconciliation using Phase 1 functions, and compares the five full-data metrics against `outputs/metrics.json`. Changed or missing baseline artifacts stop the app with an instruction to regenerate and review Phase 1. Cached source data is keyed by the current source and baseline file hashes, preventing stale cached results when those files change. The inspection reference date remains `2026-11-01` and does not restrict the date filter.

Unavailable values are not replaced by zero. Missing Profit disables Profit and margin cards and profit charts while preserving available Sales. Missing required Sales or Order ID fields produce an actionable message and stop calculations cleanly. Missing optional date, region, category, or subcategory fields are disclosed and their related controls/views are unavailable. Empty selections show a message and allow header-only CSV downloads. Zero Sales shows an explicitly undefined Profit Margin.

### Phase 2 Validation and Limits

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m pip check
```

The combined suite covers Phase 1 definitions plus full-data baseline equality, combined date/region/category filtering, inclusive dates, partial months, missing-date inclusion, calendar gaps, empty selections, zero Sales, missing fields, and unchanged source data. Streamlit AppTest exercises real widgets, reset behavior, incomplete date selection, download-button actions, and the actual CSV bytes passed to download buttons. Serialized Plotly values and displayed detail rows are checked against those same exports.

The local server was started and HTTP availability was checked. The Phase 2 dashboard was subsequently manually accepted by the project owner before Phase 3 development. The existing overview figures and calculations are preserved. AppTest checks complement that manual acceptance; they do not establish visual acceptance of the newly added Drivers tab.

Implementation references: [Streamlit AppTest](https://docs.streamlit.io/develop/api-reference/app-testing/st.testing.v1.apptest), [Streamlit downloads](https://docs.streamlit.io/develop/api-reference/widgets/st.download_button), and [Plotly bar charts](https://plotly.com/python/bar-charts/).

## Phase 3: Performance Drivers

Open the **Performance Drivers** tab in the same local dashboard. Select a Baseline month and Comparison month. Both slices use the same sidebar Region and product Category filters, and both are intersected with the sidebar date window. Month selections affect the Drivers tab only; the overview cards, detail table, and Phase 2 downloads continue to describe the entire sidebar window.

### Default Months and Coverage

Month choices come from observed source dates inside the sidebar date window, independently of the selected regions and product categories. Defaults prefer the latest two observed months whose calendar start and end both lie inside the intersection of the source date span and sidebar date window. For this workbook's default coverage, that selects **2026-10 and 2026-11**; December is not preferred because source dates end on December 30.

This is a conservative calendar-coverage heuristic, **not proof of data completeness**. The workbook has no ingestion cutoff, refresh contract, or expected-day calendar. The interface always states that completeness is unverified. If two full calendar windows are unavailable, defaults use the latest two observed months and disclose truncation. With fewer than two observed months, the comparison area prompts the user to expand the date range. Sample periods are not capped by the computer's current date or the Phase 1 inspection reference date.

Each period shows its effective inclusive date boundaries and the earliest/latest matching recorded Order Dates. The period-scope CSV preserves these separately. Missing/invalid dates cannot be assigned to a month and are excluded from Drivers, even when included in the overview. Different month lengths, seasonality, and unverified source completeness limit business interpretation. No extrapolation to a full month is performed.

### Change Definitions

| Output | Definition and handling |
| --- | --- |
| Sales and Profit | Reuse `calculate_metrics()` on each filtered period. |
| Absolute change | Comparison amount minus baseline amount. |
| Change rate | Absolute change / baseline amount, only for a **positive** baseline. A zero baseline gives no rate. For a negative baseline the rate is deliberately omitted because its denominator can reverse the apparent direction; the absolute change remains visible. |
| Profit Margin | Each period's total Profit / total Sales under Phase 1 availability rules. |
| Margin change | `(comparison margin - baseline margin) * 100`, reported in **percentage points**. No relative percentage change or average line margin is substituted. |
| Empty recorded slice | A source-observed month with no matching lines after region/product filtering has zero recorded additive totals when the source field exists. Its margin remains unavailable. This does not establish zero real-world business activity. |
| Missing/incomplete measure | Missing Profit remains unavailable, not zero. Existing rows with unknown amounts keep their partial/unavailable statuses; their affected change and contribution ranking are unavailable rather than fabricated. |

The comparison CSV stores `change_rate` as a fraction, `margin_change_pp` in percentage points, source-unit amounts at calculation precision, and availability statuses alongside each result.

### Contribution Decomposition and Summaries

Region and Sub-Category are decomposed independently for both Sales and Profit. Each row contains its baseline amount, comparison amount, and change. The model outer-joins the two period summaries: a group absent from one period gets zero **only on that absent side**. Unknown amounts in an existing group are not zero-filled. Null and whitespace-only dimension values remain in an unassigned bucket, identified by `is_missing_group` and exported as `__UNASSIGNED__`. If a dimension field is entirely absent, all records remain in that unassigned bucket with an explicit disclosure.

Four sorted signed bar charts display Region Sales/Profit changes and Sub-Category Sales/Profit changes. Positive and negative contributions have contrasting fills and signed labels. The largest strictly positive and strictly negative contributors are identified separately, including ties. No-change or unavailable rankings display a message rather than an artificial leaderboard.

For each dimension and measure, baseline sums, comparison sums, and the sum of changes are reconciled to their overall equivalents: **12 checks** when both measures are available. Amount tolerances match Phase 1 (`1e-6` absolute and `1e-12` relative). An unavailable measure is reported as `UNAVAILABLE`; a failed reconciliation stops contribution calculation. Region and Sub-Category decompositions are alternative views and must not be added together.

The short English business summary is generated from the period metrics and verified category changes. It describes recorded changes and arithmetic contributions only. It does not assert demand, marketing, pricing, or cost causes. Loss-to-profit transitions are identified directly from the signs of the two recorded Profit values.

### Downloads and Repeatable Export

The Drivers tab offers period comparison, Region contributions, Sub-Category contributions, effective period scope, reconciliation, and context downloads. These use the same DriverModel as the visible comparison and charts. Source data is unchanged and no source rows are automatically deduplicated.

For a repeatable default export without opening Streamlit:

```powershell
.\.venv\Scripts\python.exe scripts\run_drivers.py
```

To choose periods and apply matching dimension filters:

```powershell
.\.venv\Scripts\python.exe scripts\run_drivers.py --baseline-month 2026-10 --comparison-month 2026-11 --regions West --categories Furniture
```

Optional `--start-date` and `--end-date` apply the same date-window intersection as the sidebar. `--regions` or `--categories` without values intentionally selects no groups. Generated files overwrite the named comparison outputs under `outputs/drivers/`: `period_comparison.csv`, `period_scope.csv`, `region_contributions.csv`, `subcategory_contributions.csv`, `drivers_reconciliation.csv`, `drivers_context.json`, and `business_summary.md`. The context export records selected months, filters, period scope, handling rules, and the source hash for CLI runs.

### Phase 3 Validation

No additional dependencies are needed. Use the same startup and test commands as Phase 2. The Phase 1-3 and browser-repair suite has 45 tests, including 13 Drivers checks and 4 browser-acceptance regression checks; the full suite including Phase 4 has 62 tests. Drivers checks cover normal changes, percentage-point margin changes, zero baseline, loss-to-profit, one-period-only groups, missing groups, existing unknown amounts, missing Profit, empty recorded slices, truncated date windows, chart/export consistency, and same-month prompts. AppTest exercises month selectors, shared dimension filters, CSV download actions, and preservation of overview downloads when only the comparison months change.

For the default **2026-10 to 2026-11** sample comparison:

| Measure | Baseline | Comparison | Change |
| --- | ---: | ---: | ---: |
| Sales | 83,474.7832 | 118,454.5050 | +34,979.7218 (+41.9045%) |
| Profit | 10,670.5312 | 9,692.1037 | -978.4275 (-9.1694%) |
| Profit Margin | 12.78294% | 8.18213% | -4.60081 percentage points |

All 12 contribution checks pass for this comparison. These are sample-period results with unverified ingestion completeness, not verified current operating performance. Automated validation covers computed and serialized chart values and download bytes. The newly added Drivers area's rendered layout, mouse interaction, and browser file-save behavior still require browser/manual review; Phase 2's existing manual acceptance is preserved.

## Browser Acceptance Repairs

The reported reset and readability defects are addressed without changing metric definitions or source data:

- **Reset filters:** the button callback explicitly writes the full date range, all regions, all categories, undated-row inclusion, and default Drivers months back to their widget keys. Removing a Session State key can reconnect a widget to its previous browser value, so reset no longer relies on key deletion. See [Streamlit widget lifecycle](https://docs.streamlit.io/develop/concepts/architecture/widget-behavior#calling-a-widget-command-when-the-widget-already-exists).
- **KPI amounts:** the four cards use two columns per row. Metric text uses a bounded responsive font size and can wrap instead of rendering an ellipsis. Full comma-separated amounts with two decimal places remain unchanged.
- **Monthly dates:** Plotly selects calendar ticks automatically for the available axis width, with a maximum target of four labels. Month and year use separate lines, replacing the fixed tick array whose appended last date could crowd the previous label. All monthly observations, endpoint data points, gaps, and hover values remain intact.
- **Bar labels:** horizontal bars reserve 25% of the data span beyond nonzero extremes, keep zero in the axis range, allocate additional outer margin, and use outside labels with axis clipping disabled. The shared repair applies to contribution bars and existing regional/subcategory bars; values and rankings are unchanged.

The four new tests verify that reset sends explicit frontend `set_value` instructions, that widgets match the calculation/export context after reset, and that subsequent region-only, category-only, and date-only changes remain consistent. Undated-row checkbox reset is covered separately. Chart checks preserve values and calendar gaps while checking label space and tick density for signed, positive-only, negative-only, and zero ranges.

All 45 automated tests pass. These checks verify state messages, calculations, exports, and chart specifications. They do not establish pixel-level acceptance at a specific browser width; responsive typography, visible tick spacing, and label clipping still need rendered browser review when browser automation is available.

## Phase 4: Scenario Planning

The **Scenario Planning** tab uses the same current `DashboardModel` as the overview cards, selected details, and existing exports. It does not rebuild financial totals inside the UI. Before adding this phase, all 45 existing regression tests were rerun successfully, including the reset-state and chart-specification checks. The existing two-column KPI layout, automatic monthly tick density, and horizontal label padding are preserved. Browser-width visual acceptance remains unverified where automation is unavailable.

### Model Meaning and Applicability

This is **simplified sensitivity analysis, not a forecast**. Baseline Sales and Profit are recorded sample amounts for the current sidebar selection. Their difference is defined only as an **Implied Cost Proxy**:

```text
S = selected baseline Sales
P = selected baseline Profit
C = S - P = Implied Cost Proxy
```

The proxy is not verified COGS or actual cost. Source Quantity is not used to infer a real unit cost. The Cost per unit change control is an assumption about the proxy, not a measurement of actual source-data cost per unit.

The visible model limitations are:

- Product mix remains unchanged.
- Selling price and sales volume changes are independent assumptions; no demand response is estimated.
- All of the cost proxy changes proportionally with sales volume.
- Fixed and variable costs are not separated.
- Currency, tax treatment, and the source Profit cost basis remain unconfirmed.

The model is not applicable when the selection is empty, Sales is nonpositive, either financial measure is absent/non-finite/incomplete, or the derived cost proxy is negative. In those cases, the page shows the reason and does not fabricate modeled results. A recorded baseline loss is allowed if Sales is positive and its cost proxy is nonnegative.

### Assumption Controls and Formulas

Each assumption starts at **0%**, uses 1-percentage-point steps, and is stored in the model as a fractional change. Demonstration ranges are illustrative, not validated business bounds:

| Control | Demonstration range |
| --- | --- |
| Selling price change | -50% to +50% |
| Sales volume change | -100% to +100% |
| Cost per unit change | -50% to +100% |

For price change `p`, volume change `v`, and proxy unit-cost change `c`:

```text
Scenario Sales = S * (1 + p) * (1 + v)
Scenario Cost Proxy = C * (1 + v) * (1 + c)
Scenario Profit = Scenario Sales - Scenario Cost Proxy
Scenario Profit Margin = Scenario Profit / Scenario Sales
Margin change (percentage points) = 100 * (Scenario Margin - Baseline Margin)
```

Scenario Sales of zero produces an unavailable/null scenario margin and margin change. It is not converted to a 0% margin. Multiplicative arithmetic is evaluated with Decimal inputs before conversion to numeric output; displayed amounts are rounded separately. All-zero assumptions reproduce the unrounded baseline.

**Reset assumptions** explicitly writes 0% back to all three widgets and leaves filters unchanged. Assumptions persist when date, region, or category filters change; the baseline and scenario are recalculated immediately for the new selection. **Reset filters** restores the full baseline selection and default Drivers months but leaves scenario assumptions unchanged. Both reset buttons have stable, separate widget keys, and tests target those keys rather than their position on the page.

### Views and Downloads

Baseline dates, selected regions/categories, recorded line count, undated-line treatment, and the three active assumptions are shown directly in the Scenario Planning area. The side-by-side table displays Baseline, Scenario, and Change for Sales, Implied Cost Proxy, Profit, and Profit Margin. Margin change uses percentage points. A grouped amount chart compares the first three measures on the same source-unit scale; a percentage is never mixed onto that amount axis.

The short explanation uses **"Under these assumptions"** and describes only conditional sensitivity results. It makes no demand, marketing, cost-measurement, or forecasting claim.

Two downloads use the same scenario object as the table and chart:

- `scenario_results.csv`: four metric rows with baseline, scenario, change, units, the three assumptions, baseline date bounds, serialized full filter context, formulas, applicability, and model limitations.
- `scenario_results.json`: structured baseline metrics, fractional assumptions and demonstration ranges, modeled results, changes, complete filter context, formulas, limitations, explanation, and margin availability status.

Margin levels are exported as fractions, and margin changes as percentage points. Missing/undefined outputs are blank in CSV and null in JSON. Invalid baselines do not enable scenario-result downloads.

### Run and Validate

No new packages or deployment are needed. From the repository root in PowerShell:

```powershell
.\.venv\Scripts\python.exe -m streamlit run dashboard\app.py --server.address 127.0.0.1 --server.port 8501
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Open [http://127.0.0.1:8501](http://127.0.0.1:8501) and select Scenario Planning.

All **62 tests pass**, including 17 Scenario Planning checks. Explicit calculation cases use `S=1000`, `P=200`, and `C=800`:

| Assumptions | Scenario Sales | Scenario Cost Proxy | Scenario Profit |
| --- | ---: | ---: | ---: |
| All 0% | 1,000 | 800 | 200 |
| Price +10% only | 1,100 | 800 | 300 |
| Volume +10% only | 1,100 | 880 | 220 |
| Cost per unit +10% only | 1,000 | 880 | 120 |
| Volume -100% only | 0 | 0 | 0 |

The last case has null Profit Margin. Other checks cover combined assumptions, baseline losses, invalid baselines, incomplete measures, source-data default identity, assumption reset messages, filter changes, empty-selection recovery, and consistency between displayed values, Plotly traces, and the actual download payloads. Earlier Performance, Drivers, and reset/readability regressions remain in the full suite.

Automated state and chart-data validation does not establish pixel-level browser acceptance. The repaired layout and the new scenario controls/chart still require visual review at the intended browser width when browser access is available.

## Streamlit Community Cloud Deployment

Deployment entry point: **`dashboard/app.py`**. The application reads the bundled workbook and reviewed Phase 1 baseline on startup. It does not regenerate audit outputs on the hosted server. All runtime paths are derived from `Path(__file__).resolve()` and the repository root; no user-specific Windows path or `.venv` location is required. Historical notebook execution outputs are not runtime configuration.

The source workbook, `retail_lab/`, `dashboard/`, and existing `outputs/` files are already tracked by Git. Required startup baseline files are `outputs/metrics.json`, `outputs/run_manifest.json`, and `outputs/reconciliation.csv`. Preserve the other audit exports for inspection. The legacy binary workbook is about 3.4 MB and is stored directly in Git. Local environments and `.streamlit/secrets.toml` are ignored. This app requires no credentials or external data connections.

Before deploying, run and review the analysis and tests locally using the commands above. The source hash must match the manifest and the default metrics must reconcile with the committed baseline. Data-quality checks, definitions, coverage warnings, noncausal Drivers summaries, and Scenario Planning assumptions and limitations remain active in hosted runs. `Sales - Profit` remains an **Implied Cost Proxy**, not verified COGS or actual costs. Scenario results remain sensitivity analysis, not forecasts.

Commit only the deployment changes from the repository root in PowerShell. The commands below assume the current `main` branch and existing `origin` remote (`https://github.com/Xuelinghang/retail-sales-performance-analysis.git`):

```powershell
git status --short
git add -- .gitignore .streamlit/config.toml README.md requirements.txt requirements-dev.txt dashboard/app.py scripts/run_analysis.py scripts/run_drivers.py tests/test_deployment.py outputs/run_manifest.json
git diff --cached --check
git diff --cached --stat
git commit -m "Prepare dashboard for Streamlit Community Cloud"
git push origin main
```

These commands intentionally leave the pre-existing deletion of `data/.gitkeep` out of this commit. The raw workbook and other previously committed source/baseline files do not need to be added again. No commit or push is performed by the preparation process itself.

After pushing:

1. Sign in to [Streamlit Community Cloud](https://share.streamlit.io/) with access to the repository and choose **Create app**.
2. Select repository `Xuelinghang/retail-sales-performance-analysis`, branch `main`, and main file path `dashboard/app.py` (case-sensitive).
3. In **Advanced settings**, explicitly select **Python 3.14**. Do not depend on the platform default. No secrets are required.
4. Deploy and inspect the build/startup logs. The root `requirements.txt` supplies runtime dependencies. The platform manages the server address and port; do not copy the local `--server.address 127.0.0.1` command into hosting configuration.
5. Verify full-data Sales `2,326,534.35`, Profit `292,296.81`, margin `12.56%`, and Order Count `5,111`. Check combined filters and Reset filters, both Drivers periods and exports, and the zero-assumption Scenario baseline, reset behavior, and result downloads. Review chart labels at the intended browser width.

See the official [deployment guide](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy) and [dependency guide](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/app-dependencies). The platform uses Linux; successful Windows validation does not establish Linux or hosted runtime compatibility. If Python 3.14 is unavailable in your account's selector, validate a supported alternative in a matching clean environment before switching versions.

### Clean Environment Validation

Create a separate environment without inheriting the existing project packages:

```powershell
py -3.14 -m venv .venv-cloud
.\.venv-cloud\Scripts\python.exe -m pip install -r requirements.txt
.\.venv-cloud\Scripts\python.exe -m pip check
.\.venv-cloud\Scripts\python.exe scripts\run_analysis.py
.\.venv-cloud\Scripts\python.exe scripts\run_drivers.py
.\.venv-cloud\Scripts\python.exe -m unittest discover -s tests -v
.\.venv-cloud\Scripts\python.exe -m streamlit run dashboard\app.py --server.address 127.0.0.1 --server.port 8502 --server.headless true
```

Use [http://127.0.0.1:8502](http://127.0.0.1:8502) for this separate validation server. `.venv-cloud` is ignored by Git. The deployment tests execute the actual app entry point and the analysis script from another working directory, compare recalculated metrics with the committed baseline, and confirm the raw source remains unchanged. Optional notebook tools are outside the runtime-only validation scope.

Observed preparation results on November 2, 2026:

- A new Windows Python 3.14.6 environment with `include-system-site-packages = false` successfully installed only `requirements.txt`; `pip check` reported no broken requirements. The original `.venv` was retained.
- Phase 1 ran successfully with 12 `PASS` and 3 informational reconciliation rows; Drivers ran successfully with 12 `PASS`. Financial outputs matched the existing baseline. The raw workbook SHA-256 remained `5045ed4fec49cae2cc0dfc5ab0d204ba09efa767fe00c996822499e8cc42fbbd`.
- All **64 tests passed** in 31.132 seconds: the existing 62 checks plus two deployment entry-point checks. The log is local and ignored at `.venv-cloud/test-results.log`.
- The clean-environment server started on port 8502; `/` and `/_stcore/health` returned HTTP 200. AppTest executed the real app, verified the baseline Sales card, and confirmed the Scenario Planning tab was present without application exceptions.
- Existing source files, workbook, and baseline exports are Git-tracked. The new development dependency file and deployment test need the explicit `git add` command above. Generated audit metadata now records the interpreter filename rather than a personal absolute path.
- Not verified: Linux dependency installation/execution, a Community Cloud build or hosted session, a fresh installation of optional notebook dependencies, and manual browser appearance/downloads on the hosted platform. No cloud URL exists from this preparation, and no commit, push, or deployment was performed.

## SQL Analysis with DuckDB

This optional command-line module demonstrates SQL calculations from **order-line detail**, calendar-aware window functions, and guarded many-to-one enrichment. DuckDB **2.0.0** was installed and tested on Windows Python 3.14.6. It is pinned only in `requirements-sql.txt`; the Dashboard runtime file and app entry point do not import DuckDB. Installing SQL dependencies is not required to run the existing hosted Dashboard. This change does not deploy or alter the hosted app.

### Inputs, Field Mapping, and Independence

The runner calls the existing signature-aware Excel loader and registers three normalized detail frames as `orders_input`, `people_input`, and `returns_input`. Their grains remain order line, regional manager mapping, and order-level return flag. The original workbook is never converted, renamed, or overwritten.

`outputs/sql/column_mapping.csv` records every original field, its snake_case SQL name, presence, and row-level normalization rule. Examples: `Order ID -> order_id`, `Sub-Category -> sub_category`, and `Regional Manager -> regional_manager`. Shared `finite_numbers`, `parse_dates`, and `missing_mask` functions convert unusable numbers, invalid dates, and blank/whitespace-only identifiers or classifications to SQL NULL. Other nonblank text is preserved, including case and surrounding whitespace. `_source_row_number` is a derived physical data-row ordinal, independent of the source `Row ID`, so even duplicate source identifiers can be traced through joins. Missing optional columns are represented explicitly as NULL, and `source_schema` records whether Profit exists.

No Python totals, monthly tables, or category summaries are registered or used as SQL inputs. DuckDB computes aggregates and windows from selected normalized detail. The pandas verification path independently selects source rows and calls the established metric functions, builds a pandas calendar and prior-month comparisons, computes dense ranks, and performs validated pandas merges. Thus aggregation, filtering, ranking, and joining are checked across two engines; workbook reading and row-level type/missingness policy are intentionally shared rather than independently reinterpreted.

### Queries and Business Questions

| SQL file | Business question | Input and output grain | SQL techniques and handling |
| --- | --- | --- | --- |
| `00_selected_orders.sql` | Which order lines belong to the requested scope? | Registered order lines -> selected order lines | Reads a parameter table populated with bound values; one scope feeds all four analyses. |
| `01_kpi_summary.sql` | What Sales, Profit, margin, and order activity are recorded? | Selected order lines -> one KPI row | `SUM`, `COUNT(*)`, `COUNT(DISTINCT order_id)`, and availability statuses. Margin is total Profit / total Sales, never an average of line margins. |
| `02_monthly_trends.sql` | How did recorded Sales and Profit change from the immediately preceding calendar month? | Selected order lines -> one row per calendar month plus one undated bucket | CTEs aggregate detail, generate a complete monthly calendar, then apply `LAG`. Includes prior values, absolute changes, rates, and status columns. |
| `03_product_profitability.sql` | Which subcategories contribute profit or losses? | Selected order lines -> one row per subcategory, including NULL | `GROUP BY` retains missing classifications with `is_missing_group`. `DENSE_RANK` orders complete Profit totals; ties share a rank without gaps. Incomplete or unavailable Profit is not ranked or asserted to be a verified loss. |
| `04_order_enrichment.sql` | Can manager and order-level return information be added safely? | Selected order lines -> exactly the same order lines with lookup attributes | SQL checks full lookup tables for nonmissing unique keys before either `LEFT JOIN`. An unsafe key raises an error before the join; no automatic deduplication is performed. |

**Missing values and ratios:** SQL NULL amounts remain unknown. `SUM` returns the known amount with a `partial` status if some selected inputs are unusable, or NULL if none are usable. Missing Profit is `field_missing`. Profit Margin is NULL when inputs are incomplete, Profit is absent, or Sales is zero; zero division is also guarded with `NULLIF`. Null or whitespace-only order IDs do not contribute to distinct order counts, but their lines and usable amounts remain in totals. Category-level order counts are not additive; actual distinct order sets are reconciled instead.

**Monthly calendar and changes:** The calendar spans the requested date window when both endpoints are supplied, otherwise the selected usable date bounds. Missing calendar months have `record_status=no_records`, zero recorded line/order counts, and **NULL Sales and Profit**, not claimed zero business activity. Because `LAG` runs after calendar completion, March cannot accidentally use January as its previous month when February has no records. The separate `record_type=undated` row contains selected missing/invalid dates and amounts; it is excluded from calendar windows but included in coverage reconciliation, even when its row count is zero. Without usable dates or explicit bounds, only that bucket is returned.

Change rates require complete current/prior measures and a **positive** prior value. A zero prior value yields `previous_zero`; a negative prior value yields `previous_negative`. Absolute changes remain available for complete observed inputs in both cases, while percentage changes remain NULL. Missing records or incomplete amounts have explicit statuses and do not generate invented changes. Rates are exported as fractions. Date-window edges and source coverage may truncate calendar months; monthly results use only selected records and do not establish ingestion completeness or extrapolate full months.

**Safe enrichment:** `People.Region` and `Returns.Order ID` are checked across their entire source sheets, independently of selected filters. Null and whitespace-only right-side keys are rejected as well as duplicates. SQL records before/after join line counts, Sales, and Profit, and pandas independently verifies every line identity, enrichment attribute, and order set. Unmatched manager or return attributes stay NULL. A return-list match applies an order-level flag to each matching order line; it does not identify returned product quantities, refund amounts, or net sales. An unmatched order does not prove that no return occurred.

### Install and Run

From the repository root in PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-sql.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe scripts\run_sql_analysis.py

# The same filters are bound once and used by every query and pandas check.
.\.venv\Scripts\python.exe scripts\run_sql_analysis.py --start-date 2026-10-01 --end-date 2026-11-30 --regions West --categories Furniture --exclude-undated --output-dir .venv/sql-filtered

# SQL-only checks; then the full suite including the Dashboard regressions.
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_sql_analysis.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Both date endpoints must be supplied together, using `YYYY-MM-DD`; the end date is inclusive. With no dates there is no date restriction. Missing/invalid dates are included by default, consistent with the Dashboard; `--exclude-undated` removes them explicitly. `--regions` and `--categories` accept multiple exact source values; omitting a flag means unrestricted, while specifying it with no values produces an empty selection. `(Unassigned)` selects a missing region/category. Date and filter values are passed through DuckDB parameter binding, never concatenated into SQL text; even a quote-containing filter is treated as data.

The runner resolves the source and `.sql` files relative to its own project location, so launching it by absolute path from another working directory is supported and tested. `--output-dir` is optional, defaults to project `outputs/sql/`, and a relative override is resolved from the current working directory. Writing SQL output inside `data/` is blocked. Unsafe joins, query errors, source changes, or reconciliation failures return a nonzero exit code. Failure is recorded in `run_manifest.json`; if a failed run uses an existing destination, older files there are explicitly marked as not current rather than presented as successful results.

### Outputs and Reconciliation

| File under `outputs/sql/` | Purpose |
| --- | --- |
| `kpi_summary.csv` | SQL totals, counts, margin, and availability statuses |
| `monthly_trends.csv` | Complete calendar, separate undated totals, prior-month values and change statuses |
| `product_profitability.csv` | Subcategory totals, missing-group flag, dense Profit rank, and loss flag |
| `order_enrichment.csv` | Normalized selected detail with regional manager and return-list attributes |
| `join_key_checks.csv`, `join_preservation.csv` | Lookup key diagnostics and SQL totals before/after LEFT JOIN |
| `selected_order_ids.csv`, `group_order_ids.csv` | Actual distinct order sets, including per-month and per-subcategory membership |
| `column_mapping.csv` | Original-to-SQL field mapping and row-level normalization |
| `reconciliation.csv` | SQL/pandas values and statuses, exact group keys and order-set/line-identity agreement |
| `run_manifest.json` | Success/failure, filters, query hashes, DuckDB version, source hashes, tolerances, and limitations |

Values are not rounded before calculation or comparison. Amounts use absolute tolerance `1e-6` and relative tolerance `1e-12`; ratios use absolute and relative tolerance `1e-12`. Small differences can arise because SQL floating-point `SUM` and Python `math.fsum` use different accumulation algorithms. Missing outputs must match as missing, not as zero. Monetary reconciliation can pass for partial sums without proving that unknown source amounts are complete or that flagged records are correct.

Observed Windows validation on November 2, 2026: all four queries executed successfully; full-data SQL outputs passed **1,448 checks** against pandas, including grouped values, statuses, order sets and join preservation. Full-data results are Sales **2,326,534.35**, Profit **292,296.81**, Profit Margin **12.56%**, **5,111** distinct orders and **10,194** lines (display rounding only). People has 4 valid unique keys and Returns has 296; both joins preserve all line identities and totals. The filtered example above passed **162 checks**, with Sales **20,080.141**, Profit **361.4796**, 49 orders and 60 lines. The raw Excel hash remains unchanged.

All **79 tests passed** in 39.361 seconds with DuckDB installed, including the existing 64 analysis/Dashboard/deployment checks. The 15 SQL tests cover duplicate and blank lookup keys, zero division, invalid/all-missing dates, calendar gaps, missing/nonfinite amounts, absent Profit, missing categories, dense-rank ties, zero/negative prior bases, empty/filter selections, a quote-containing filter, deliberately corrupted SQL, nonzero CLI failure codes, and execution from another working directory. Without DuckDB installed, only these optional tests are skipped; the existing Dashboard tests remain runnable with `requirements.txt` alone. Linux DuckDB execution and hosted SQL execution have not been verified; this module is a local CLI extension.
