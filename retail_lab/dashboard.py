"""Shared filtered data model, downloads, and Plotly figures for the local app."""

from dataclasses import dataclass
from datetime import date
import json
import math
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go

from retail_lab.analysis import (
    ABSOLUTE_TOLERANCE, RELATIVE_TOLERANCE, aggregate_dimension,
    analyze_workbook, calculate_metrics, file_hash, load_workbook, missing_mask,
    parse_dates, prepare_orders, profile_workbook, reconcile_dimension,
)

REFERENCE_DATE = "2026-11-01"
UNASSIGNED = "(Unassigned)"
SALES_COLOR = "#286486"
PROFIT_COLOR = "#416A59"
LOSS_COLOR = "#B44F33"
AMOUNT_UNIT = "Source amount units"


@dataclass
class DashboardModel:
    details: pd.DataFrame
    metrics: dict
    summaries: dict
    reconciliation: pd.DataFrame
    quality: dict
    context: dict
    partial_months: list


def date_bounds(orders):
    if "Order Date" not in orders:
        return None
    dates = parse_dates(orders["Order Date"]).dropna()
    return (dates.min().date(), dates.max().date()) if len(dates) else None


def dimension_options(orders, column):
    if column not in orders:
        return []
    values = orders[column].mask(missing_mask(orders[column]))
    options = sorted(values.dropna().astype(str).unique().tolist())
    if values.isna().any():
        options.append(UNASSIGNED)
    return options


def filter_orders(orders, start=None, end=None, regions=None, categories=None, include_undated=True):
    """One selection feeds cards, figures, table, quality checks, and downloads."""
    view = prepare_orders(orders)
    mask = pd.Series(True, index=view.index)
    if start is not None and end is not None:
        dates = parse_dates(orders.get("Order Date", pd.Series(pd.NaT, index=orders.index)))
        dated = dates.ge(pd.Timestamp(start)) & dates.lt(pd.Timestamp(end) + pd.Timedelta(days=1))
        mask &= dated | (dates.isna() if include_undated else False)
    elif not include_undated:
        dates = parse_dates(orders.get("Order Date", pd.Series(pd.NaT, index=orders.index)))
        mask &= dates.notna()
    for column, selected in [("_region", regions), ("_category", categories)]:
        if selected is not None:
            values = view[column].astype("string").fillna(UNASSIGNED)
            mask &= values.isin(selected)
    return view.loc[mask].copy()


def partial_months(start, end):
    if start is None or end is None:
        return []
    first, last = pd.Timestamp(start), pd.Timestamp(end)
    months = []
    if first.day != 1:
        months.append(first.strftime("%b %Y"))
    if last.date() != (last + pd.offsets.MonthEnd(0)).date():
        months.append(last.strftime("%b %Y"))
    return list(dict.fromkeys(months))


def build_dashboard_model(orders, start=None, end=None, regions=None, categories=None, include_undated=True):
    view = filter_orders(orders, start, end, regions, categories, include_undated)
    details = view[list(orders.columns)].copy()
    metrics = calculate_metrics(view)
    summaries, checks = {}, []
    dimensions = [("monthly", "_month", "Month"), ("regional", "_region", "Region"),
                  ("category", "_category", "Category")]
    if "Sub-Category" in view:
        view["_subcategory"] = view["Sub-Category"].mask(missing_mask(view["Sub-Category"]))
        dimensions.append(("subcategory", "_subcategory", "Sub-Category"))
    for name, dimension, label in dimensions:
        summary = aggregate_dimension(view, dimension, label)
        summaries[name] = summary
        checks.extend(reconcile_dimension(view, summary, dimension, label, metrics))
    reconciliation = pd.DataFrame(checks)
    if reconciliation["status"].eq("FAIL").any():
        raise ValueError("Filtered summaries did not reconcile; inspect the shared analysis functions.")
    context = {
        "start_date": str(start) if start is not None else None,
        "end_date_inclusive": str(end) if end is not None else None,
        "regions": regions, "categories": categories,
        "include_missing_or_invalid_order_dates": include_undated,
        "amount_unit": AMOUNT_UNIT, "currency_confirmed": False,
        "inspection_reference_date": REFERENCE_DATE,
        "order_line_count": metrics["order_line_count"], "metrics": metrics,
        "undated_order_line_count": int(view["_month"].isna().sum()),
    }
    return DashboardModel(details, metrics, summaries, reconciliation,
                          profile_workbook({"Orders": details}, pd.Timestamp(REFERENCE_DATE)),
                          context, partial_months(start, end))


def load_verified_source(project_root):
    """Check the current source and recalculated totals against Phase 1 files."""
    root = Path(project_root)
    source_path = root / "data/raw/sample_-_superstore.xlsx"
    outputs = root / "outputs"
    required = [outputs / name for name in ["metrics.json", "run_manifest.json", "reconciliation.csv"]]
    if any(not path.is_file() for path in required):
        raise ValueError("Phase 1 outputs are missing. Run scripts/run_analysis.py first.")
    source_hash = file_hash(source_path)
    manifest = json.loads(required[1].read_text(encoding="utf-8"))
    if source_hash != manifest["source_sha256_after"] or manifest.get("reconciliation_failed"):
        raise ValueError("The source or Phase 1 validation has changed. Run scripts/run_analysis.py before opening the dashboard.")
    saved_checks = pd.read_csv(required[2])
    if saved_checks["status"].eq("FAIL").any():
        raise ValueError("Phase 1 reconciliation contains failures. Resolve them before opening the dashboard.")
    sheets = load_workbook(source_path)
    metrics, quality = analyze_workbook(sheets, REFERENCE_DATE)
    if file_hash(source_path) != source_hash:
        raise ValueError("Source changed while loading. Retry after the workbook is stable.")
    baseline = json.loads(required[0].read_text(encoding="utf-8"))
    for name in ["sales", "profit", "profit_margin", "order_count", "order_line_count"]:
        expected, observed = baseline[name], metrics[name]
        if expected is None or observed is None:
            matches = expected is observed
        elif name in {"order_count", "order_line_count"}:
            matches = expected == observed
        else:
            tolerance = 1e-12 if name == "profit_margin" else ABSOLUTE_TOLERANCE
            matches = math.isclose(expected, observed, abs_tol=tolerance, rel_tol=RELATIVE_TOLERANCE)
        if not matches:
            raise ValueError(f"Default {name} does not match Phase 1. Regenerate and review the baseline.")
    if quality["reconciliation"]["status"].eq("FAIL").any():
        raise ValueError("Current source reconciliation failed. Resolve it before using the dashboard.")
    return sheets["Orders"], quality, source_hash


def csv_bytes(frame, group_column=None):
    exported = frame.copy()
    if group_column is not None:
        exported[group_column] = exported[group_column].astype("string").fillna("__UNASSIGNED__")
    return exported.to_csv(index=False).encode("utf-8")


def download_payloads(model):
    payloads = {"order_lines": csv_bytes(model.details)}
    for name, column in [("monthly", "Month"), ("regional", "Region"), ("category", "Category")]:
        payloads[name] = csv_bytes(model.summaries[name], column)
    payloads["selection_context"] = json.dumps(model.context, indent=2, allow_nan=False).encode("utf-8")
    return payloads


def style_figure(figure, title, height=340):
    figure.update_layout(
        title={"text": title, "font": {"size": 17}, "x": 0},
        height=height, autosize=True, margin={"l": 10, "r": 25, "t": 55, "b": 35},
        font={"family": "Arial, sans-serif", "size": 13, "color": "#263B4A"},
        paper_bgcolor="white", plot_bgcolor="white", showlegend=False,
        hoverlabel={"bgcolor": "white", "font_color": "#263B4A"},
    )
    figure.update_xaxes(showgrid=False, automargin=True, zerolinecolor="#8997A1")
    figure.update_yaxes(gridcolor="#E9EDF1", zerolinecolor="#8997A1", automargin=True)
    return figure


def pad_horizontal_bar_labels(figure, values):
    """Reserve space for full numeric labels at either end of signed bars."""
    finite = [float(value) for value in values if value is not None and math.isfinite(value)]
    if not finite:
        return figure
    lower, upper = min(0.0, min(finite)), max(0.0, max(finite))
    spread = upper - lower or 1.0
    padding = spread * 0.25
    limits = [lower - padding if lower < 0 else 0, upper + padding if upper > 0 else 0]
    if lower == upper:
        limits = [-padding, padding]
    label_width = max(len(f"{value:+,.0f}") for value in finite) * 7 + 16
    figure.update_traces(textposition="outside", cliponaxis=False, textfont={"size": 12})
    figure.update_xaxes(range=limits, autorange=False)
    figure.update_layout(margin={"l": 24, "r": max(80, label_width), "t": 55, "b": 45})
    return figure


def trend_figure(model, metric):
    monthly = model.summaries["monthly"]
    dated = monthly.loc[monthly["Month"].notna()].copy()
    if dated.empty or dated[metric].notna().sum() == 0:
        return None
    dated.index = pd.to_datetime(dated["Month"] + "-01")
    first = pd.Timestamp(model.context["start_date"]) if model.context["start_date"] else dated.index.min()
    last = pd.Timestamp(model.context["end_date_inclusive"]) if model.context["end_date_inclusive"] else dated.index.max()
    months = pd.date_range(first.to_period("M").to_timestamp(), last.to_period("M").to_timestamp(), freq="MS")
    plotted = dated.reindex(months)
    values = [None if pd.isna(value) else float(value) for value in plotted[metric]]
    color = SALES_COLOR if metric == "sales" else PROFIT_COLOR
    figure = go.Figure(go.Scatter(
        x=[value.strftime("%Y-%m-%d") for value in months], y=values,
        mode="lines+markers", connectgaps=False, line={"color": color, "width": 2},
        marker={"size": 5, "color": color},
        hovertemplate="%{x|%b %Y}<br>" + metric.title() + ": %{y:,.2f} source units<extra></extra>",
    ))
    # Plotly chooses calendar ticks using the rendered axis width. A small
    # upper bound and two-line labels avoid crowded endpoint tick pairs.
    figure.update_xaxes(type="date", tickmode="auto", nticks=4,
                        tickformat="%b<br>%Y", tickangle=0, automargin=True)
    figure.update_yaxes(title=AMOUNT_UNIT, tickformat="~s", rangemode="tozero")
    return style_figure(figure, f"Monthly {metric}")


def regional_figure(model, metric):
    regional = model.summaries["regional"].copy()
    if regional.empty or regional[metric].notna().sum() == 0:
        return None
    regional["Region"] = regional["Region"].astype("string").fillna(UNASSIGNED)
    regional = regional.sort_values("Region", ascending=False)
    values = [None if pd.isna(value) else float(value) for value in regional[metric]]
    figure = go.Figure(go.Bar(
        x=values, y=regional["Region"].tolist(), orientation="h",
        marker_color=SALES_COLOR if metric == "sales" else PROFIT_COLOR,
        text=values, texttemplate="%{text:,.0f}", textposition="auto",
        hovertemplate="%{y}<br>" + metric.title() + ": %{x:,.2f} source units<extra></extra>",
    ))
    figure.update_xaxes(title=AMOUNT_UNIT, tickformat="~s", rangemode="tozero")
    style_figure(figure, f"Regional {metric}", 310)
    return pad_horizontal_bar_labels(figure, values)


def subcategory_figure(model):
    ranking = model.summaries.get("subcategory")
    if ranking is None or ranking.empty or ranking["profit"].notna().sum() == 0:
        return None
    ranking = ranking.sort_values("profit", ascending=True, na_position="last").copy()
    ranking["Sub-Category"] = ranking["Sub-Category"].astype("string").fillna(UNASSIGNED)
    values = [None if pd.isna(value) else float(value) for value in ranking["profit"]]
    labels = [f"{label} (loss)" if value is not None and value < 0 else label
              for label, value in zip(ranking["Sub-Category"], values)]
    figure = go.Figure(go.Bar(
        x=values, y=labels, orientation="h",
        marker_color=[LOSS_COLOR if value is not None and value < 0 else PROFIT_COLOR for value in values],
        text=values, texttemplate="%{text:,.0f}", textposition="auto",
        hovertemplate="%{y}<br>Profit: %{x:,.2f} source units<extra></extra>",
    ))
    figure.update_xaxes(title=AMOUNT_UNIT, tickformat="~s", rangemode="tozero", zeroline=True, zerolinewidth=1)
    style_figure(figure, "Subcategory profit ranking", max(360, len(ranking) * 27 + 95))
    return pad_horizontal_bar_labels(figure, values)


def build_figures(model):
    return {
        "monthly_sales": trend_figure(model, "sales"),
        "monthly_profit": trend_figure(model, "profit"),
        "regional_sales": regional_figure(model, "sales") if "Region" in model.details else None,
        "regional_profit": regional_figure(model, "profit") if "Region" in model.details else None,
        "subcategory_profit": subcategory_figure(model),
    }
