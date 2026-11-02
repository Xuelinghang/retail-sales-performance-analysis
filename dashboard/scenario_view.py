"""Scenario Planning UI using the current shared DashboardModel."""

import pandas as pd
import streamlit as st

from retail_lab.scenario import (
    ASSUMPTION_CONTROLS, FORMULAS, LIMITATIONS, build_scenario_model,
    scenario_downloads, scenario_figure,
)


def reset_assumptions():
    # Explicit widget writes also update the browser; do not delete keys.
    for _, _, key, _, _ in ASSUMPTION_CONTROLS:
        st.session_state[key] = 0


def selection_label(values):
    return "Unrestricted / field unavailable" if values is None else ", ".join(values) or "None selected"


def render_scenario_planning(dashboard_model):
    st.subheader("Scenario Planning")
    st.info("Simplified sensitivity analysis, not a forecast. Product mix is unchanged; price and volume assumptions are independent. All cost proxy scales with volume; fixed and variable costs are not separated.")
    st.caption("Implied Cost Proxy = Sales - Profit. This is not verified COGS or actual cost. Cost per unit change is an input assumption, not an observed source-data unit cost.")
    context = dashboard_model.context
    window = f"{context['start_date']} to {context['end_date_inclusive']} inclusive" if context["start_date"] else "No usable date restriction"
    st.markdown(f"**Baseline scope:** {window}. **Regions:** {selection_label(context['regions'])}. "
                f"**Product categories:** {selection_label(context['categories'])}. "
                f"**Recorded lines:** {context['order_line_count']:,}.")
    st.caption(f"Missing/invalid date inclusion: {context['include_missing_or_invalid_order_dates']}. "
               f"Unassigned-date lines in baseline: {context['undated_order_line_count']:,}. Amounts use unconfirmed source currency units.")
    st.button("Reset assumptions", key="reset_assumptions", on_click=reset_assumptions)
    rates = []
    for column, (name, label, key, low, high) in zip(st.columns(3), ASSUMPTION_CONTROLS):
        with column:
            percent = st.slider(label, min_value=low, max_value=high, value=0, step=1,
                                format="%d%%", key=key,
                                help=f"Illustrative range: {low}% to +{high}%. This is a model assumption, not an estimate from the data.")
            rates.append(percent / 100.0)
    st.caption(f"Current assumptions: selling price {rates[0]:+.0%}; sales volume {rates[1]:+.0%}; cost per unit {rates[2]:+.0%}. "
               "Assumptions persist when filters change. Reset assumptions restores 0% without changing filters.")
    model = build_scenario_model(dashboard_model, *rates)
    if not model.applicable:
        st.info(model.explanation)
        return
    display_rows = []
    for row in model.comparison.to_dict("records"):
        margin = row["metric"] == "Profit Margin"
        def display_value(value):
            return "Unavailable" if pd.isna(value) else (f"{value:.2%}" if margin else f"{value:,.2f}")
        change = "Unavailable" if pd.isna(row["change"]) else f"{row['change']:+,.2f}" + (" pp" if margin else "")
        display_rows.append({"Metric": row["metric"], "Baseline": display_value(row["baseline_value"]),
                             "Scenario": display_value(row["scenario_value"]), "Change": change})
    st.dataframe(pd.DataFrame(display_rows), hide_index=True, width="stretch")
    st.caption("Amount changes are in source units. Profit Margin change is in percentage points.")
    st.plotly_chart(scenario_figure(model), width="stretch", key="scenario_comparison", theme=None,
                    config={"displaylogo": False})
    st.markdown(model.explanation)
    with st.expander("Calculation definitions and full model limitations"):
        for name, formula in FORMULAS.items():
            st.code(f"{name}: {formula}", language="text")
        st.caption("S = baseline Sales; P = baseline Profit; C = S - P; p, v, c are the three fractional change assumptions.")
        for limitation in LIMITATIONS:
            st.markdown("- " + limitation)
    payloads = scenario_downloads(model)
    for column, extension, mime in zip(st.columns(2), ["csv", "json"], ["text/csv", "application/json"]):
        with column:
            st.download_button(f"Download scenario results ({extension.upper()})", payloads[f"scenario_results_{extension}"],
                               file_name=f"scenario_results.{extension}", mime=mime,
                               key=f"download_scenario_{extension}", on_click="ignore")
