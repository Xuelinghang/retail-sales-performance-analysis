"""Performance Drivers presentation; all calculations live in retail_lab."""

import pandas as pd
import streamlit as st

from retail_lab.drivers import (
    build_driver_model, contribution_figure, driver_downloads,
    largest_contributors, month_plan,
)


def render_performance_drivers(orders, start, end, regions, categories):
    st.subheader("Performance Drivers")
    st.caption("Compare two monthly slices within the sidebar date window, using the same region and product-category filters. Overview cards above describe the entire sidebar window.")
    plan = month_plan(orders, start, end)
    if plan["defaults"] is None:
        st.info("At least two observed months are needed within the sidebar date window. Expand the date range to compare periods.")
        return
    st.warning("Month completeness is unverified: transaction dates alone do not establish that all records were loaded. Defaults prefer the latest two observed months fully inside the available calendar window; this is not a completeness guarantee.")
    if len(plan["calendar_months"]) < 2:
        st.info("Two full calendar windows are unavailable. Default selections use the latest available months and may be truncated by the date filter or source coverage.")
    selections = []
    for column, key, label, default in zip(st.columns(2), ["drivers_baseline_month", "drivers_comparison_month"],
                                          ["Baseline month", "Comparison month"], plan["defaults"]):
        if key in st.session_state and st.session_state[key] not in plan["months"]:
            st.session_state.pop(key)
        with column:
            selections.append(st.selectbox(label, plan["months"], index=plan["months"].index(default), key=key))
    if selections[0] == selections[1]:
        st.info("Select two different months to compare performance.")
        return
    model = build_driver_model(orders, *selections, start, end, regions, categories)
    for row in model.periods.to_dict("records"):
        observed = f"{row['observed_start']} to {row['observed_end']}" if row["observed_start"] else "No matching recorded lines"
        st.caption(f"{row['period'].title()} ({row['month']}): {row['effective_start']} to {row['effective_end_inclusive']} inclusive; observed order dates: {observed}; {row['order_line_count']:,} lines.")
    if not model.periods["full_calendar_window"].all():
        st.warning("At least one selected month is truncated. This is a comparison of the effective date ranges shown above, not two complete calendar months.")
    st.caption("Undated lines cannot be assigned to either month and are excluded here. Months may differ in length or seasonality. Amounts use unconfirmed source currency units.")
    display_rows = []
    for row in model.comparison.to_dict("records"):
        is_margin = row["metric"] == "profit_margin"
        def display_value(value):
            return "Unavailable" if pd.isna(value) else (f"{value:.2%}" if is_margin else f"{value:,.2f}")
        change = row["margin_change_pp"] if is_margin else row["absolute_change"]
        change_text = "Unavailable" if pd.isna(change) else f"{change:+,.2f}" + (" pp" if is_margin else " source units")
        rate = f"{row['change_rate']:+.2%}" if row["change_rate_status"] == "available" else {
            "zero_baseline": "Unavailable: zero baseline", "negative_baseline": "Omitted: negative baseline",
            "not_applicable": "Not applicable", "unavailable": "Unavailable",
        }[row["change_rate_status"]]
        display_rows.append({"Metric": row["metric"].replace("_", " ").title(),
                             "Baseline": display_value(row["baseline_value"]), "Comparison": display_value(row["comparison_value"]),
                             "Change": change_text, "Change rate": rate})
    st.dataframe(pd.DataFrame(display_rows), hide_index=True, width="stretch")
    with st.expander("Comparison definitions and handling rules"):
        st.markdown("Sales and Profit use the shared sum definitions. Changes are comparison minus baseline. Rates use change / baseline only when the baseline is positive; a negative denominator can reverse the apparent direction, so absolute change is shown instead. Margin change is (comparison margin - baseline margin) x 100, in percentage points. Only absent groups are filled with zero; unknown amounts in existing groups remain unavailable. A month with no matching recorded lines has zero recorded amounts when the field exists, not a verified zero-business-activity claim. Missing dimensions stay in an unassigned bucket. Contribution amounts are arithmetic attributions, not causal explanations.")
    st.markdown("**Business summary**")
    for sentence in model.summary:
        st.markdown("- " + sentence)
    if model.context["missing_dimension_fields"]:
        st.info("Missing dimension fields are shown as unassigned: " + ", ".join(model.context["missing_dimension_fields"]))
    for dimension, label in [("region", "Region"), ("subcategory", "Sub-Category")]:
        st.markdown(f"**{label} contributions**")
        table = model.contributions[dimension]
        for column, measure in zip(st.columns(2), ["sales", "profit"]):
            with column:
                figure = contribution_figure(model, dimension, measure)
                if figure is not None:
                    st.plotly_chart(figure, key=f"drivers_{dimension}_{measure}", width="stretch", theme=None, config={"displaylogo": False})
                    extremes = largest_contributors(table, measure)
                    for direction, item in extremes.items():
                        st.caption(f"Largest {direction}: " + (f"{', '.join(item['groups'])} ({item['change']:+,.2f} source units)" if item else "None"))
                else:
                    st.info(f"{label} {measure} changes: no nonzero complete contributions to plot.")
        with st.expander(f"View {label} baseline, comparison, and changes"):
            shown = table.copy()
            shown[label] = shown[label].astype("string").fillna("__UNASSIGNED__")
            st.dataframe(shown[[label, "baseline_sales", "comparison_sales", "sales_change", "baseline_profit", "comparison_profit", "profit_change", "is_missing_group"]], hide_index=True, width="stretch")
    with st.expander("Contribution reconciliation"):
        st.dataframe(model.reconciliation, hide_index=True, width="stretch")
    st.markdown("**Download comparison results**")
    payloads = driver_downloads(model)
    for column, name, label in zip(st.columns(3), ["period_comparison", "region_contributions", "subcategory_contributions"],
                                  ["Download period comparison", "Download Region contributions", "Download Sub-Category contributions"]):
        with column:
            st.download_button(label, payloads[name], file_name=f"{name}_{selections[0]}_to_{selections[1]}.csv", mime="text/csv", key=f"download_{name}", on_click="ignore")
    with st.expander("Download scope and validation details"):
        for name in ["period_scope", "drivers_reconciliation", "drivers_context"]:
            extension, mime = ("json", "application/json") if name == "drivers_context" else ("csv", "text/csv")
            st.download_button(f"Download {name.replace('_', ' ')}", payloads[name], file_name=f"{name}.{extension}", mime=mime, key=f"download_{name}", on_click="ignore")
