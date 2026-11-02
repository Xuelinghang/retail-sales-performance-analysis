"""Streamlit dashboard for local use and managed hosting."""

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
import streamlit as st

from retail_lab.analysis import file_hash, parse_dates
from retail_lab.dashboard import (
    AMOUNT_UNIT, REFERENCE_DATE, build_dashboard_model, build_figures,
    date_bounds, dimension_options, download_payloads, load_verified_source,
)
from dashboard.drivers_view import render_performance_drivers
from dashboard.scenario_view import render_scenario_planning
from retail_lab.drivers import month_plan


@st.cache_data(show_spinner="Loading and validating the sample workbook...", max_entries=2)
def cached_source(source_signature, baseline_signature):
    return load_verified_source(PROJECT_ROOT)


def reset_filters(bounds, regions, categories, driver_defaults):
    # Explicit assignments mark widget values as changed, so Streamlit also
    # sends the restored values to the browser. Deleting keys does not do this.
    if bounds is not None:
        st.session_state["date_range"] = tuple(bounds)
    st.session_state["regions"] = list(regions)
    st.session_state["categories"] = list(categories)
    st.session_state["include_undated"] = True
    if driver_defaults is not None:
        st.session_state["drivers_baseline_month"] = driver_defaults[0]
        st.session_state["drivers_comparison_month"] = driver_defaults[1]


def amount(value):
    return "Unavailable" if value is None else f"{value:,.2f}"


def render_dashboard(orders, source_quality=None):
    st.markdown("""<style>
    [data-testid="stMetricValue"], [data-testid="stMetricValue"] > div {
        font-size: clamp(1.15rem, 2.2vw, 2rem);
        font-variant-numeric: tabular-nums;
        white-space: normal !important;
        overflow: visible !important;
        text-overflow: clip !important;
        overflow-wrap: anywhere;
    }
    </style>""", unsafe_allow_html=True)
    st.title("Retail Performance & Planning Lab")
    st.caption("Analyst application portfolio project using Superstore sample data. For demonstration, not verified operating results.")
    st.caption("Finance review | Amounts in source units; currency, tax treatment, and profit cost basis are unconfirmed.")
    missing_required = sorted({"Sales", "Order ID"} - set(orders.columns))
    if missing_required:
        st.error("Required fields are missing: " + ", ".join(missing_required) + ". Restore the source schema before calculating metrics.")
        st.stop()

    bounds = date_bounds(orders)
    region_options = dimension_options(orders, "Region")
    category_options = dimension_options(orders, "Category")
    driver_defaults = month_plan(orders)["defaults"]
    with st.sidebar:
        st.header("Filters")
        st.button("Reset filters", key="reset_filters", on_click=reset_filters,
                  args=(bounds, region_options, category_options, driver_defaults))
        if bounds:
            date_range = st.date_input("Order date range", value=bounds, min_value=bounds[0], max_value=bounds[1],
                                       format="YYYY-MM-DD", key="date_range")
            if date_range is None or len(date_range) != 2:
                st.info("Select both a start date and an end date.")
                st.stop()
            start, end = date_range
        else:
            start = end = None
            st.info("Order dates are missing or invalid. Date filtering and monthly trends are unavailable.")
        regions = st.multiselect("Regions", region_options, default=region_options,
                                 key="regions", disabled="Region" not in orders)
        categories = st.multiselect("Product categories", category_options, default=category_options,
                                    key="categories", disabled="Category" not in orders)
        st.caption("All listed values are selected by default. Clearing a selection returns no records.")
        invalid_date_count = int(parse_dates(orders["Order Date"]).isna().sum()) if "Order Date" in orders else len(orders)
        include_undated = True
        if invalid_date_count:
            include_undated = st.checkbox("Include missing or invalid order dates", value=True, key="include_undated")
            st.caption(f"{invalid_date_count:,} source lines have no usable order date. Included by default to preserve coverage.")
        st.caption(f"Quality inspection reference: {REFERENCE_DATE}. This is not a date filter.")
    for field in ["Profit", "Order Date", "Region", "Category", "Sub-Category"]:
        if field not in orders:
            st.warning(f"{field} field is missing. Related metrics, filters, or charts are unavailable.")
    model = build_dashboard_model(
        orders, start, end, regions if "Region" in orders else None,
        categories if "Category" in orders else None, include_undated,
    )
    # The entire UI consumes this one selection snapshot.
    st.session_state["selection_context"] = model.context
    coverage = f"{bounds[0].isoformat()} to {bounds[1].isoformat()}" if bounds else "Unavailable"
    scope = f"{start} to {end} (inclusive)" if bounds else "No usable date restriction"
    st.caption(f"Source order-date coverage: {coverage}. Selected window: {scope}.")
    st.caption(f"Regions: {', '.join(regions) or ('Unavailable' if 'Region' not in orders else 'None')}. "
               f"Categories: {', '.join(categories) or ('Unavailable' if 'Category' not in orders else 'None')}. "
               f"Selected lines: {len(model.details):,} of {len(orders):,}.")

    metric_labels = [("Sales", amount(model.metrics["sales"])),
                     ("Profit", amount(model.metrics["profit"])),
                     ("Profit Margin", "Unavailable" if model.metrics["profit_margin"] is None else f"{model.metrics['profit_margin']:.2%}"),
                     ("Order Count", f"{model.metrics['order_count']:,}")]
    for offset in range(0, len(metric_labels), 2):
        for column, (label, value) in zip(st.columns(2), metric_labels[offset:offset + 2]):
            with column.container(border=True):
                st.metric(label, value)
    if not model.details.empty and (model.metrics["sales_status"] != "complete" or model.metrics["profit_status"] not in {"complete", "field_missing"}):
        st.warning("Some amounts are missing or invalid. Available sums are partial; Profit Margin is unavailable when inputs are incomplete.")
    if model.metrics["profit_margin_status"] == "zero_sales":
        st.info("Profit Margin is unavailable because total Sales is zero.")
    with st.expander("Metric definitions and interpretation"):
        st.markdown("""- **Sales:** sum of finite numeric Sales values on selected order lines.
- **Profit:** sum of finite numeric Profit values. An absent field is unavailable, not zero.
- **Profit Margin:** total Profit / total Sales. Never an average of row margins. Unavailable for zero Sales or incomplete inputs.
- **Order Count:** distinct nonmissing Order ID values in the selection.
- **Order Line Count:** all selected source rows, including review flags.

Selecting a category measures its order lines, not the full baskets of matching orders. An order can appear in several categories, so grouped order counts are not additive. No returns adjustments or manager joins are applied to amounts.""")

    performance, drivers, scenario, quality, details = st.tabs([
        "Performance", "Performance Drivers", "Scenario Planning", "Data quality", "Details & exports"])
    with performance:
        if model.details.empty:
            st.info("No records match the current filters. Expand the date range or select a region and product category.")
        else:
            if model.partial_months:
                st.warning("Partial-month date coverage: " + ", ".join(model.partial_months) + ". Monthly totals use only selected dates; compare complete months with care.")
            st.caption("Monthly sales and profit use separate vertical scales. Gaps indicate months with no usable selected values, not measured zeros.")
            if model.context["undated_order_line_count"]:
                st.caption(f"{model.context['undated_order_line_count']:,} lines with unassigned order dates remain in metrics and exports but cannot be placed on the monthly time axis.")
            figures = build_figures(model)
            for left_name, right_name in [("monthly_sales", "monthly_profit"), ("regional_sales", "regional_profit")]:
                for column, name in zip(st.columns(2), [left_name, right_name]):
                    with column:
                        if figures[name] is None:
                            st.info(name.replace("_", " ").title() + " is unavailable for this selection.")
                        else:
                            st.plotly_chart(figures[name], width="stretch", key=name, theme=None,
                                            config={"displaylogo": False})
            if figures["subcategory_profit"] is not None:
                st.plotly_chart(figures["subcategory_profit"], width="stretch", key="subcategory_profit", theme=None,
                                config={"displaylogo": False})
                st.caption("Loss-making subcategories use a contrasting fill and a '(loss)' label. Values show total Profit in source units.")
            else:
                st.info("Subcategory profit ranking is unavailable: no usable Profit or Sub-Category field.")
    with quality:
        st.subheader("Current selection")
        st.caption("All source rows remain intact. Negative profit, duplicate candidates, future dates, and IQR review flags are retained. IQR bounds are recalculated for this selection.")
        selected_checks = model.quality["quality_checks"]
        active_checks = selected_checks.loc[selected_checks["count"].gt(0)]
        if active_checks.empty:
            st.info("No nonzero quality flags in the current selection. This does not establish business accuracy.")
        else:
            st.dataframe(active_checks[["field", "check", "count", "row_denominator", "rate", "interpretation"]], hide_index=True, width="stretch")
        st.dataframe(model.quality["duplicate_summary"][["duplicate_type", "extra_rows", "affected_rows", "interpretation"]], hide_index=True, width="stretch")
        with st.expander("Phase 1 source-wide audit (fixed baseline)"):
            st.caption("This audit describes the entire workbook, independently of current filters.")
            if source_quality is not None:
                baseline_checks = source_quality["quality_checks"]
                st.dataframe(baseline_checks.loc[baseline_checks["count"].gt(0), ["sheet", "field", "check", "count", "row_denominator", "interpretation"]], hide_index=True, width="stretch")
                st.dataframe(source_quality["duplicate_summary"][["sheet", "duplicate_type", "extra_rows", "affected_rows"]], hide_index=True, width="stretch")
                st.dataframe(source_quality["relationships"], hide_index=True, width="stretch")
            else:
                st.info("Source-wide audit is unavailable for this input.")
        with st.expander("Handling rules and selected-scope reconciliation"):
            st.markdown("Missing/invalid amounts are excluded only from their affected sum and never imputed as zero. Missing grouping values are retained as unassigned. Invalid dates cannot be shown on a time axis. Duplicate flags do not trigger deletion. The Returns sheet has no returned quantity, amount, or date; no net-sales adjustment is inferred.")
            st.dataframe(model.reconciliation, hide_index=True, width="stretch")
    with details:
        st.caption(f"{len(model.details):,} selected source lines. Table sorting/search changes the table view only; downloads include the complete filter selection.")
        displayed_details = model.details.copy()
        if "Postal Code" in displayed_details:
            displayed_details["Postal Code"] = displayed_details["Postal Code"].astype("string")
        st.dataframe(displayed_details, hide_index=True, width="stretch", height=420)
        payloads = download_payloads(model)
        for column, name, label in zip(st.columns(4), ["order_lines", "monthly", "regional", "category"],
                                        ["Download order lines", "Download monthly summary", "Download regional summary", "Download category summary"]):
            with column:
                st.download_button(label, payloads[name], file_name=f"retail_{name}_{start or 'all'}_{end or 'all'}.csv",
                                   mime="text/csv", key=f"download_{name}", on_click="ignore")
        st.download_button("Download selection context", payloads["selection_context"], file_name="retail_selection_context.json",
                           mime="application/json", key="download_context", on_click="ignore")
    with drivers:
        render_performance_drivers(orders, start, end, regions if "Region" in orders else None,
                                   categories if "Category" in orders else None)
    with scenario:
        render_scenario_planning(model)


def main():
    st.set_page_config(page_title="Retail Performance & Planning Lab", page_icon="📊", layout="wide")
    try:
        source = PROJECT_ROOT / "data/raw/sample_-_superstore.xlsx"
        baseline_files = [PROJECT_ROOT / "outputs" / name for name in ["metrics.json", "run_manifest.json", "reconciliation.csv"]]
        orders, quality, source_hash = cached_source(file_hash(source), tuple(file_hash(path) for path in baseline_files))
    except (OSError, ValueError, KeyError) as error:
        st.error(f"Source validation could not complete: {error}")
        st.info("Run python scripts/run_analysis.py from the repository root, review the outputs, and commit the validated baseline before deploying or reloading.")
        st.stop()
    render_dashboard(orders, quality)


if __name__ == "__main__":
    main()
