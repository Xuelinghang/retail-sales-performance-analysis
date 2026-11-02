"""Simplified price/volume/cost-proxy sensitivity, not a forecast."""

from dataclasses import dataclass
from decimal import Decimal
import json
import math

import pandas as pd
import plotly.graph_objects as go

from retail_lab.dashboard import AMOUNT_UNIT, SALES_COLOR, csv_bytes, pad_horizontal_bar_labels, style_figure

METRICS = ["sales", "implied_cost_proxy", "profit", "profit_margin"]
LABELS = {"sales": "Sales", "implied_cost_proxy": "Implied Cost Proxy",
          "profit": "Profit", "profit_margin": "Profit Margin"}
ASSUMPTION_CONTROLS = [
    ("selling_price_change", "Selling price change", "scenario_price_pct", -50, 50),
    ("sales_volume_change", "Sales volume change", "scenario_volume_pct", -100, 100),
    ("cost_per_unit_change", "Cost per unit change", "scenario_cost_pct", -50, 100),
]
LIMITATIONS = [
    "This is a simplified sensitivity analysis, not a forecast.",
    "Product mix is assumed unchanged.",
    "Selling price and sales volume changes are independent assumptions; no demand response is estimated.",
    "All of the Implied Cost Proxy changes proportionally with sales volume.",
    "Fixed and variable costs are not separated.",
    "Sales minus Profit is an Implied Cost Proxy, not verified COGS or actual cost.",
    "Cost per unit change is a model assumption, not a real unit cost identified from the source data.",
    "Amounts use unconfirmed source currency units and are not verified realized operating results.",
]
FORMULAS = {
    "baseline_cost_proxy": "C = S - P",
    "scenario_sales": "S * (1 + p) * (1 + v)",
    "scenario_cost_proxy": "C * (1 + v) * (1 + c)",
    "scenario_profit": "Scenario Sales - Scenario Cost Proxy",
    "scenario_profit_margin": "Scenario Profit / Scenario Sales; unavailable when Scenario Sales is zero",
    "margin_change_pp": "100 * (Scenario Profit Margin - Baseline Profit Margin)",
}


@dataclass
class ScenarioModel:
    applicable: bool
    reasons: list
    baseline: dict
    assumptions: dict
    scenario: dict
    changes: dict
    context: dict
    comparison: pd.DataFrame
    explanation: str


def finite_value(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def calculate_scenario(sales, profit, price_change=0.0, volume_change=0.0, cost_change=0.0,
                       context=None, baseline_issues=()):
    """Rates are fractions. Decimal arithmetic avoids default-case noise."""
    assumptions = dict(zip(["selling_price_change", "sales_volume_change", "cost_per_unit_change"],
                           [price_change, volume_change, cost_change]))
    for name, value in assumptions.items():
        number = finite_value(value)
        if number is None or number < -1:
            raise ValueError(f"{name} must be finite and no less than -100%.")
        assumptions[name] = number
    sales, profit = finite_value(sales), finite_value(profit)
    reasons = list(baseline_issues)
    if sales is None or profit is None:
        reasons.append("Baseline Sales and Profit must both be present and finite.")
    if sales is not None and sales <= 0:
        reasons.append("Baseline Sales must be greater than zero.")
    cost = None
    if sales is not None and profit is not None:
        cost = float(Decimal(str(sales)) - Decimal(str(profit)))
        if not math.isfinite(cost):
            reasons.append("The Implied Cost Proxy is not finite.")
            cost = None
        elif cost < 0:
            reasons.append("Implied Cost Proxy is negative (Profit exceeds Sales); this model is not applicable.")
    baseline = {"sales": sales, "implied_cost_proxy": cost, "profit": profit,
                "profit_margin": profit / sales if sales is not None and sales > 0 and profit is not None else None}
    scenario = {metric: None for metric in METRICS}
    changes = {metric: None for metric in ["sales", "implied_cost_proxy", "profit", "profit_margin_pp"]}
    if not reasons:
        source_sales, source_profit = Decimal(str(sales)), Decimal(str(profit))
        source_cost = source_sales - source_profit
        price, volume, unit_cost = [Decimal(str(assumptions[name])) for name in assumptions]
        modeled_sales = source_sales * (1 + price) * (1 + volume)
        modeled_cost = source_cost * (1 + volume) * (1 + unit_cost)
        modeled_profit = modeled_sales - modeled_cost
        scenario = {"sales": float(modeled_sales), "implied_cost_proxy": float(modeled_cost),
                    "profit": float(modeled_profit), "profit_margin": None}
        if not all(math.isfinite(scenario[metric]) for metric in METRICS[:-1]):
            reasons.append("The assumptions produce non-finite amounts; reduce their magnitude.")
            scenario = {metric: None for metric in METRICS}
        else:
            scenario["profit_margin"] = scenario["profit"] / scenario["sales"] if scenario["sales"] != 0 else None
            for metric in ["sales", "implied_cost_proxy", "profit"]:
                changes[metric] = scenario[metric] - baseline[metric]
            if scenario["profit_margin"] is not None:
                changes["profit_margin_pp"] = (scenario["profit_margin"] - baseline["profit_margin"]) * 100
    rows = [{"metric": LABELS[metric], "baseline_value": baseline[metric], "scenario_value": scenario[metric],
             "change": changes["profit_margin_pp" if metric == "profit_margin" else metric],
             "value_unit": "fraction" if metric == "profit_margin" else AMOUNT_UNIT,
             "change_unit": "percentage points" if metric == "profit_margin" else AMOUNT_UNIT}
            for metric in METRICS]
    result = ScenarioModel(not reasons, list(dict.fromkeys(reasons)), baseline, assumptions,
                           scenario, changes, dict(context or {}), pd.DataFrame(rows), "")
    if result.applicable:
        margin_sentence = (
            f"Profit Margin changes from {baseline['profit_margin']:.2%} to {scenario['profit_margin']:.2%} "
            f"({changes['profit_margin_pp']:+.2f} percentage points)."
            if scenario["profit_margin"] is not None else
            "Scenario Profit Margin is unavailable because Scenario Sales is zero."
        )
        result.explanation = (
            f"Under these assumptions, Scenario Sales is {scenario['sales']:,.2f}, "
            f"Scenario Implied Cost Proxy is {scenario['implied_cost_proxy']:,.2f}, "
            f"and Scenario Profit is {scenario['profit']:,.2f} "
            f"(a change of {changes['profit']:+,.2f} source units). {margin_sentence} "
            "These are sensitivity results, not a forecast."
        )
    else:
        result.explanation = "Model not applicable: " + " ".join(result.reasons)
    return result


def build_scenario_model(dashboard_model, price_change=0.0, volume_change=0.0, cost_change=0.0):
    metrics = dashboard_model.metrics
    issues = []
    if metrics["order_line_count"] == 0:
        issues.append("The current filter selection has no recorded order lines.")
    for measure in ["sales", "profit"]:
        if metrics[f"{measure}_status"] != "complete":
            issues.append(f"Baseline {measure.title()} is missing or incomplete; unknown amounts cannot be treated as zero.")
    return calculate_scenario(metrics["sales"], metrics["profit"], price_change, volume_change, cost_change,
                              dashboard_model.context, issues)


def scenario_downloads(model):
    document = {
        "model": "Simplified price-volume-cost-proxy sensitivity", "applicable": model.applicable,
        "not_applicable_reasons": model.reasons, "baseline": model.baseline,
        "assumptions_as_fractional_changes": model.assumptions,
        "demonstration_ranges_percent": {name: [low, high] for name, _, _, low, high in ASSUMPTION_CONTROLS},
        "scenario": model.scenario, "change": model.changes, "filters": model.context,
        "definitions": FORMULAS, "limitations": LIMITATIONS, "explanation": model.explanation,
        "scenario_margin_status": "unavailable" if not model.applicable else (
            "zero_scenario_sales" if model.scenario["profit_margin"] is None else "available"),
    }
    exported = model.comparison.copy()
    for key, value in model.assumptions.items():
        exported[key] = value
    exported["model_applicable"] = model.applicable
    exported["baseline_start_date"] = model.context.get("start_date")
    exported["baseline_end_date_inclusive"] = model.context.get("end_date_inclusive")
    exported["filters_json"] = json.dumps(model.context, allow_nan=False)
    exported["model_definitions_json"] = json.dumps(FORMULAS)
    exported["model_limitations"] = " | ".join(LIMITATIONS)
    return {"scenario_results_csv": csv_bytes(exported),
            "scenario_results_json": json.dumps(document, indent=2, allow_nan=False).encode("utf-8")}


def scenario_figure(model):
    if not model.applicable:
        return None
    figure = go.Figure()
    labels = [LABELS[metric] for metric in METRICS[:-1]]
    all_values = []
    for name, values, color in [("Baseline", model.baseline, "#A0B5C4"), ("Scenario", model.scenario, SALES_COLOR)]:
        amounts = [values[metric] for metric in METRICS[:-1]]
        all_values.extend(amounts)
        figure.add_trace(go.Bar(name=name, x=amounts, y=labels, orientation="h", marker_color=color,
                                text=amounts, texttemplate="%{text:,.0f}",
                                hovertemplate="%{y}<br>%{x:,.2f} source units<extra>%{fullData.name}</extra>"))
    style_figure(figure, "Baseline vs scenario amounts", 420)
    pad_horizontal_bar_labels(figure, all_values)
    figure.update_layout(barmode="group", showlegend=True, margin={"t": 90},
                         legend={"orientation": "h", "x": 0, "y": 1.06},
                         meta={"scope": "scenario_planning"})
    figure.update_xaxes(title=AMOUNT_UNIT)
    figure.update_yaxes(autorange="reversed")
    return figure
