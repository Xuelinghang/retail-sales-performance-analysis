"""Reconciled month comparisons and arithmetic contribution analysis."""

from dataclasses import dataclass
import json
import math

import pandas as pd
import plotly.graph_objects as go

from retail_lab.analysis import (
    ABSOLUTE_TOLERANCE, RELATIVE_TOLERANCE, aggregate_dimension,
    calculate_metrics, missing_mask, parse_dates, sum_known,
)
from retail_lab.dashboard import (
    AMOUNT_UNIT, LOSS_COLOR, SALES_COLOR, csv_bytes, date_bounds,
    filter_orders, pad_horizontal_bar_labels, style_figure,
)


@dataclass
class DriverModel:
    comparison: pd.DataFrame
    periods: pd.DataFrame
    contributions: dict
    reconciliation: pd.DataFrame
    context: dict
    summary: list


def month_plan(orders, start=None, end=None):
    bounds = date_bounds(orders)
    if bounds is None:
        return {"months": [], "defaults": None, "calendar_months": [], "start": None, "end": None}
    start = max(bounds[0], pd.Timestamp(start).date()) if start is not None else bounds[0]
    end = min(bounds[1], pd.Timestamp(end).date()) if end is not None else bounds[1]
    dates = parse_dates(orders["Order Date"])
    within = dates.ge(pd.Timestamp(start)) & dates.lt(pd.Timestamp(end) + pd.Timedelta(days=1))
    months = sorted(dates.loc[within].dt.to_period("M").astype(str).unique().tolist())
    calendar_months = [month for month in months
                       if pd.Period(month, freq="M").start_time.date() >= start
                       and pd.Period(month, freq="M").end_time.date() <= end]
    eligible = calendar_months if len(calendar_months) >= 2 else months
    return {"months": months, "defaults": tuple(eligible[-2:]) if len(eligible) >= 2 else None,
            "calendar_months": calendar_months, "start": start, "end": end}


def period_metrics(view):
    metrics = calculate_metrics(view)
    if view.empty:
        # An observed month with no matching recorded lines is an empty slice,
        # distinct from a populated slice whose amount values are unknown.
        metrics["sales"] = 0.0
        metrics["sales_status"] = "empty_recorded_slice"
        if "_profit" in view:
            metrics["profit"] = 0.0
            metrics["profit_status"] = "empty_recorded_slice"
            metrics["profit_margin_status"] = "zero_sales"
    return metrics


def usable(metrics, measure):
    return metrics[f"{measure}_status"] in {"complete", "empty_recorded_slice", "absent_group_zero"}


def compare_metrics(baseline, comparison):
    rows = []
    for measure in ["sales", "profit"]:
        base, current = baseline[measure], comparison[measure]
        available = usable(baseline, measure) and usable(comparison, measure)
        change = current - base if available else None
        status = "unavailable"
        rate = None
        if available:
            if base > 0:
                rate, status = change / base, "available"
            elif base == 0:
                status = "zero_baseline"
            else:
                status = "negative_baseline"
        rows.append({"metric": measure, "baseline_value": base, "comparison_value": current,
                     "baseline_status": baseline[f"{measure}_status"],
                     "comparison_status": comparison[f"{measure}_status"],
                     "absolute_change": change, "change_rate": rate,
                     "change_rate_status": status, "margin_change_pp": None})
    base, current = baseline["profit_margin"], comparison["profit_margin"]
    rows.append({"metric": "profit_margin", "baseline_value": base, "comparison_value": current,
                 "baseline_status": baseline["profit_margin_status"],
                 "comparison_status": comparison["profit_margin_status"],
                 "absolute_change": None, "change_rate": None,
                 "change_rate_status": "not_applicable",
                 "margin_change_pp": (current - base) * 100 if base is not None and current is not None else None})
    return pd.DataFrame(rows)


def contribution_table(baseline_view, comparison_view, dimension, label):
    columns = [label, "sales", "profit", "sales_status", "profit_status", "order_line_count"]
    baseline = aggregate_dimension(baseline_view, dimension, label)[columns]
    comparison = aggregate_dimension(comparison_view, dimension, label)[columns]
    baseline = baseline.rename(columns={column: f"baseline_{column}" for column in columns if column != label})
    comparison = comparison.rename(columns={column: f"comparison_{column}" for column in columns if column != label})
    table = baseline.merge(comparison, on=label, how="outer", validate="one_to_one", indicator=True)
    table["is_missing_group"] = table[label].isna()
    for side, absent_flag, view in [("baseline", "right_only", baseline_view), ("comparison", "left_only", comparison_view)]:
        absent = table["_merge"].eq(absent_flag)
        table.loc[absent, f"{side}_order_line_count"] = 0
        for measure in ["sales", "profit"]:
            field_present = measure == "sales" or "_profit" in view
            if field_present:
                table.loc[absent, f"{side}_{measure}"] = 0.0
                table.loc[absent, f"{side}_{measure}_status"] = "absent_group_zero"
    for measure in ["sales", "profit"]:
        valid = (table[f"baseline_{measure}_status"].isin(["complete", "absent_group_zero"])
                 & table[f"comparison_{measure}_status"].isin(["complete", "absent_group_zero"]))
        table[f"{measure}_change"] = (table[f"comparison_{measure}"] - table[f"baseline_{measure}"]).where(valid)
        table[f"{measure}_change_status"] = valid.map({True: "available", False: "incomplete_or_missing_measure"})
    return table.drop(columns="_merge")


def reconcile_contributions(table, label, baseline, comparison, comparison_table):
    rows = []
    for measure in ["sales", "profit"]:
        change = comparison_table.loc[comparison_table["metric"].eq(measure), "absolute_change"].iloc[0]
        definitions = [
            ("baseline", f"baseline_{measure}", baseline[measure], usable(baseline, measure)),
            ("comparison", f"comparison_{measure}", comparison[measure], usable(comparison, measure)),
            ("change", f"{measure}_change", change, usable(baseline, measure) and usable(comparison, measure)),
        ]
        for stage, column, expected, available in definitions:
            observed = sum_known(table[column]) if len(table) else (0.0 if available else None)
            status = "UNAVAILABLE"
            if available:
                all_known = table[column].notna().all()
                status = "PASS" if all_known and observed is not None and math.isclose(
                    observed, expected, abs_tol=ABSOLUTE_TOLERANCE, rel_tol=RELATIVE_TOLERANCE
                ) else "FAIL"
            rows.append({"dimension": label, "measure": measure, "check": stage,
                         "expected": expected, "observed": observed,
                         "difference": observed - expected if observed is not None and pd.notna(expected) else None,
                         "status": status})
    return rows


def build_driver_model(orders, baseline_month, comparison_month, start=None, end=None, regions=None, categories=None):
    plan = month_plan(orders, start, end)
    if baseline_month == comparison_month:
        raise ValueError("Select two different months.")
    if baseline_month not in plan["months"] or comparison_month not in plan["months"]:
        raise ValueError("Both months must have source observations within the sidebar date window.")
    scoped = filter_orders(orders, plan["start"], plan["end"], regions, categories, include_undated=False)
    original_subcategory = scoped.get("Sub-Category", pd.Series(pd.NA, index=scoped.index, dtype="string"))
    scoped["_subcategory"] = original_subcategory.mask(missing_mask(original_subcategory))
    views = [scoped.loc[scoped["_month"].eq(month)].copy() for month in [baseline_month, comparison_month]]
    baseline, comparison = [period_metrics(view) for view in views]
    comparison_table = compare_metrics(baseline, comparison)
    period_rows = []
    for role, month, view in zip(["baseline", "comparison"], [baseline_month, comparison_month], views):
        period = pd.Period(month, freq="M")
        effective_start = max(plan["start"], period.start_time.date())
        effective_end = min(plan["end"], period.end_time.date())
        dates = parse_dates(view["Order Date"])
        period_rows.append({
            "period": role, "month": month, "effective_start": str(effective_start), "effective_end_inclusive": str(effective_end),
            "observed_start": str(dates.min().date()) if len(dates) else None,
            "observed_end": str(dates.max().date()) if len(dates) else None,
            "order_line_count": len(view),
            "full_calendar_window": effective_start == period.start_time.date() and effective_end == period.end_time.date(),
            "data_completeness": "unverified: no ingestion cutoff or expected-day calendar",
        })
    contributions, checks = {}, []
    for name, dimension, label in [("region", "_region", "Region"), ("subcategory", "_subcategory", "Sub-Category")]:
        table = contribution_table(*views, dimension, label)
        contributions[name] = table
        checks.extend(reconcile_contributions(table, label, baseline, comparison, comparison_table))
    reconciliation = pd.DataFrame(checks)
    if reconciliation["status"].eq("FAIL").any():
        raise ValueError("Contribution amounts did not reconcile to the overall comparison.")
    context = {"baseline_month": baseline_month, "comparison_month": comparison_month,
               "sidebar_start": str(plan["start"]), "sidebar_end_inclusive": str(plan["end"]),
               "regions": regions, "categories": categories, "amount_unit": AMOUNT_UNIT,
               "data_completeness_confirmed": False, "undated_rows_assigned_to_periods": False,
               "empty_period_rule": "No matching recorded lines: additive totals are zero when the source field exists; margin remains unavailable.",
               "missing_group_rule": "Only an absent group is zero-filled; missing amounts in existing groups remain unavailable.",
               "missing_dimension_fields": [column for column in ["Region", "Sub-Category"] if column not in orders]}
    model = DriverModel(comparison_table, pd.DataFrame(period_rows), contributions, reconciliation, context, [])
    model.summary = business_summary(model)
    return model


def largest_contributors(table, measure):
    values = table[f"{measure}_change"]
    if values.isna().any():
        return {"positive": None, "negative": None}
    label = "Region" if "Region" in table else "Sub-Category"
    result = {}
    for direction, selected in [("positive", values.loc[values.gt(0)]), ("negative", values.loc[values.lt(0)])]:
        if selected.empty:
            result[direction] = None
            continue
        extreme = selected.max() if direction == "positive" else selected.min()
        labels = table.loc[values.eq(extreme), label].astype("string").fillna("__UNASSIGNED__").tolist()
        result[direction] = {"groups": labels, "change": float(extreme)}
    return result


def business_summary(model):
    if model.periods["order_line_count"].sum() == 0:
        return ["Neither selected month has matching recorded order lines under the current filters."]
    sentences = []
    for row in model.comparison.to_dict("records"):
        if row["metric"] == "profit_margin":
            if pd.notna(row["margin_change_pp"]):
                sentences.append(f"Profit Margin moved from {row['baseline_value']:.2%} to {row['comparison_value']:.2%} ({row['margin_change_pp']:+.2f} percentage points).")
            else:
                sentences.append("Profit Margin change is unavailable because a period has zero Sales or missing/incomplete measures.")
        elif pd.notna(row["absolute_change"]):
            rate = f" ({row['change_rate']:+.2%})" if row["change_rate_status"] == "available" else (
                "; change rate omitted because the baseline is negative" if row["change_rate_status"] == "negative_baseline" else "; change rate unavailable because the baseline is zero")
            transition = " Profit moved from loss to profit." if row["metric"] == "profit" and row["baseline_value"] < 0 < row["comparison_value"] else ""
            sentences.append(f"{row['metric'].title()} changed from {row['baseline_value']:,.2f} to {row['comparison_value']:,.2f}, a change of {row['absolute_change']:+,.2f} source units{rate}.{transition}")
        else:
            sentences.append(f"{row['metric'].title()} change is unavailable because a period contains missing or incomplete measures.")
    for dimension, table in model.contributions.items():
        parts = []
        for measure in ["sales", "profit"]:
            if table[f"{measure}_change"].isna().any():
                parts.append(f"{measure}: contribution ranking unavailable")
                continue
            extremes = largest_contributors(table, measure)
            descriptions = []
            for direction in ["positive", "negative"]:
                item = extremes[direction]
                if item:
                    descriptions.append(f"largest {direction} contribution: {', '.join(item['groups'])} ({item['change']:+,.2f})")
            parts.append(f"{measure}: " + ("; ".join(descriptions) or "no category-level change"))
        sentences.append(("Region" if dimension == "region" else "Sub-Category") + " - " + ". ".join(parts) + ".")
    return sentences


def contribution_figure(model, dimension, measure):
    table = model.contributions[dimension]
    if table.empty or table[f"{measure}_change"].isna().any() or table[f"{measure}_change"].eq(0).all():
        return None
    label = "Region" if dimension == "region" else "Sub-Category"
    table = table.sort_values(f"{measure}_change", kind="stable")
    values = table[f"{measure}_change"].astype(float).tolist()
    labels = table[label].astype("string").fillna("__UNASSIGNED__").tolist()
    figure = go.Figure(go.Bar(
        x=values, y=labels, orientation="h",
        marker_color=[SALES_COLOR if value >= 0 else LOSS_COLOR for value in values],
        text=values, texttemplate="%{text:+,.0f}", textposition="auto",
        customdata=table[[f"baseline_{measure}", f"comparison_{measure}"]].values.tolist(),
        hovertemplate="%{y}<br>Baseline: %{customdata[0]:,.2f}<br>Comparison: %{customdata[1]:,.2f}<br>Change: %{x:+,.2f} source units<extra></extra>",
    ))
    figure.update_xaxes(title="Change in source amount units", rangemode="tozero", zeroline=True, zerolinewidth=1)
    style_figure(figure, f"{label} {measure} change", max(310, len(table) * 27 + 100))
    figure.update_layout(meta={"scope": "performance_drivers", "dimension": dimension, "measure": measure})
    return pad_horizontal_bar_labels(figure, values)


def driver_downloads(model):
    context = {**model.context, "periods": model.periods.to_dict("records"), "business_summary": model.summary}
    return {"period_comparison": csv_bytes(model.comparison), "period_scope": csv_bytes(model.periods),
            "region_contributions": csv_bytes(model.contributions["region"], "Region"),
            "subcategory_contributions": csv_bytes(model.contributions["subcategory"], "Sub-Category"),
            "drivers_reconciliation": csv_bytes(model.reconciliation),
            "drivers_context": json.dumps(context, indent=2, allow_nan=False).encode("utf-8")}
