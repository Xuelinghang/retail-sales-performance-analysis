"""Detail-level DuckDB calculations and independent pandas reconciliation."""

from dataclasses import dataclass
import math
from pathlib import Path
import re

import duckdb
import pandas as pd

from retail_lab.analysis import (
    ABSOLUTE_TOLERANCE, RELATIVE_TOLERANCE, calculate_metrics,
    finite_numbers, missing_mask, parse_dates, prepare_orders,
)
from retail_lab.dashboard import filter_orders

ROOT = Path(__file__).resolve().parents[1]
QUERY_FILES = {
    "kpi_summary": "01_kpi_summary.sql",
    "monthly_trends": "02_monthly_trends.sql",
    "product_profitability": "03_product_profitability.sql",
    "order_enrichment": "04_order_enrichment.sql",
}


class UnsafeJoinError(ValueError):
    def __init__(self, checks):
        super().__init__("Enrichment stopped: lookup keys must be unique and nonmissing.")
        self.checks = checks


@dataclass
class SqlAnalysis:
    results: dict
    reconciliation: pd.DataFrame
    mapping: pd.DataFrame
    context: dict


def normalized_inputs(sheets):
    """Only row-level typing/missingness is shared with pandas; never aggregate."""
    requirements = {"Orders": {"Sales", "Order ID"},
                    "People": {"Region", "Regional Manager"},
                    "Returns": {"Order ID", "Returned"}}
    frames, mapping = {}, []
    for sheet, required in requirements.items():
        if sheet not in sheets or required - set(sheets[sheet].columns):
            raise ValueError(f"{sheet} is missing a sheet or required fields: {sorted(required)}")
        raw = sheets[sheet].reset_index(drop=True).copy()
        # This call also enforces the existing required Orders schema.
        if sheet == "Orders":
            prepare_orders(raw)
        renamed = {column: re.sub(r"[^a-z0-9]+", "_", column.lower()).strip("_") for column in raw}
        if len(set(renamed.values())) != len(renamed) or "_source_row_number" in renamed.values():
            raise ValueError(f"SQL column-name collision in {sheet}.")
        frame = raw.rename(columns=renamed)
        for original, name in renamed.items():
            rule = "Source value retained"
            if original in {"Sales", "Profit", "Quantity", "Discount", "Row ID"}:
                frame[name] = finite_numbers(raw[original]).astype("float64")
                rule = "Shared finite_numbers; invalid/nonfinite values become NULL"
            elif original.endswith("Date"):
                frame[name] = parse_dates(raw[original])
                rule = "Shared parse_dates; missing/invalid/untyped numeric dates become NULL"
            else:
                frame[name] = raw[original].mask(missing_mask(raw[original])).astype("string")
                rule = "Shared missing_mask; blank/whitespace-only values become NULL; other text is unchanged"
            mapping.append({"sheet": sheet, "source_field": original, "sql_column": name,
                            "source_field_present": True, "normalization": rule})
        if sheet == "Orders":
            for name, dtype in [("profit", "float64"), ("order_date", "datetime64[ns]"),
                                ("region", "string"), ("category", "string"), ("sub_category", "string")]:
                if name not in frame:
                    frame[name] = pd.Series(index=frame.index, dtype=dtype)
                    mapping.append({"sheet": sheet, "source_field": None, "sql_column": name,
                                    "source_field_present": False, "normalization": "Absent optional field represented as NULL"})
            frame["_source_row_number"] = range(1, len(frame) + 1)
            mapping.append({"sheet": sheet, "source_field": None, "sql_column": "_source_row_number",
                            "source_field_present": False, "normalization": "One-based physical data-row ordinal; Excel row = ordinal + 1"})
        frames[sheet] = frame
    return frames, pd.DataFrame(mapping)


def scalar(value):
    return None if pd.isna(value) else value


def monthly_reference(view, start, end):
    """pandas calendar/shift oracle, independent of SQL's CTEs and windows."""
    dates = parse_dates(view.get("Order Date", pd.Series(pd.NaT, index=view.index)))
    first = pd.Timestamp(start) if start is not None else dates.min()
    last = pd.Timestamp(end) if end is not None else dates.max()
    periods = pd.period_range(first, last, freq="M") if pd.notna(first) and pd.notna(last) else []
    rows = []
    previous = None
    for month in periods:
        group = view.loc[view["_month"].eq(str(month)).fillna(False)]
        metrics = calculate_metrics(group)
        row = {"record_type": "calendar_month", "month": str(month), **metrics,
               "record_status": "observed" if len(group) else "no_records",
               "previous_month": previous["month"] if previous else None,
               "previous_sales": previous["sales"] if previous else None,
               "previous_profit": previous["profit"] if previous else None,
               "previous_sales_status": previous["sales_status"] if previous else None,
               "previous_profit_status": previous["profit_status"] if previous else None,
               "previous_line_count": previous["order_line_count"] if previous else None}
        for measure in ["sales", "profit"]:
            current, prior = metrics[measure], previous[measure] if previous else None
            complete = metrics[f"{measure}_status"] == "complete" and previous is not None and previous[f"{measure}_status"] == "complete"
            if not len(group):
                status = "no_records"
            elif previous is None:
                status = "no_previous_month"
            elif not previous["order_line_count"]:
                status = "previous_no_records"
            elif not complete:
                status = "incomplete_measures"
            elif prior == 0:
                status = "previous_zero"
            elif prior < 0:
                status = "previous_negative"
            else:
                status = "available"
            row[f"{measure}_mom_status"] = status
            row[f"{measure}_mom_change"] = current - prior if complete else None
            row[f"{measure}_mom_rate"] = (current - prior) / prior if status == "available" else None
        rows.append(row)
        previous = row
    empty_dates = view.loc[dates.isna()]
    rows.append({"record_type": "undated", "month": None, **calculate_metrics(empty_dates),
                 "record_status": "observed" if len(empty_dates) else "no_records",
                 **{f"previous_{key}": None for key in ["month", "sales", "profit", "sales_status", "profit_status", "line_count"]},
                 **{f"{m}_mom_{key}": "not_calendar_month" if key == "status" else None
                    for m in ["sales", "profit"] for key in ["status", "change", "rate"]}})
    return pd.DataFrame(rows)


def product_reference(view):
    values = view.get("Sub-Category", pd.Series(pd.NA, index=view.index, dtype="string"))
    derived = view.assign(_subcategory=values.mask(missing_mask(values)))
    rows = [{"sub_category": scalar(name), "is_missing_group": pd.isna(name), **calculate_metrics(group)}
            for name, group in derived.groupby("_subcategory", dropna=False, sort=True)]
    if not rows:
        return pd.DataFrame(columns=["sub_category"])
    result = pd.DataFrame(rows)
    usable = result["profit_status"].eq("complete")
    result["profit_rank"] = result["profit"].where(usable).rank(method="dense", ascending=False)
    result["is_loss"] = pd.Series(pd.NA, index=result.index, dtype="boolean")
    result.loc[usable, "is_loss"] = result.loc[usable, "profit"].lt(0)
    return result


def reconcile_results(results, orders, people, returns, filters):
    """Compare values, statuses, all group keys and actual order/line identities."""
    expected_view = filter_orders(orders.assign(_source_row_number=range(1, len(orders) + 1)), **filters)
    rows = []

    def check(scope, name, expected, observed, note=""):
        expected, observed = scalar(expected), scalar(observed)
        if expected is None or observed is None:
            same = expected is observed
        elif isinstance(expected, (float, int)) and not isinstance(expected, bool):
            tolerance = 1e-12 if name.endswith(("margin", "rate")) else ABSOLUTE_TOLERANCE
            same = math.isclose(float(expected), float(observed), abs_tol=tolerance, rel_tol=RELATIVE_TOLERANCE)
        else:
            same = expected == observed
        rows.append({"scope": scope, "check": name, "expected": expected, "observed": observed,
                     "status": "PASS" if same else "FAIL", "note": note})

    def compare_table(name, expected, observed, keys):
        def indexed(frame):
            return {tuple(scalar(row[key]) for key in keys): row for row in frame.to_dict("records")}
        left, right = indexed(expected), indexed(observed)
        check(name, "group_keys", str(sorted(left, key=str)), str(sorted(right, key=str)))
        check(name, "unique_result_keys", len(right), len(observed))
        for key in left.keys() & right.keys():
            for field in left[key]:
                if field not in keys:
                    check(f"{name}:{key}", field, left[key][field], right[key].get(field))

    expected_metrics = calculate_metrics(expected_view)
    for field, value in expected_metrics.items():
        check("kpi_summary", field, value, results["kpi_summary"].iloc[0][field])
    monthly = results["monthly_trends"].copy()
    for column in ["month", "previous_month"]:
        monthly[column] = pd.to_datetime(monthly[column]).dt.strftime("%Y-%m").where(monthly[column].notna(), None)
    compare_table("monthly_trends", monthly_reference(expected_view, filters["start"], filters["end"]), monthly,
                  ["record_type", "month"])
    compare_table("product_profitability", product_reference(expected_view), results["product_profitability"], ["sub_category"])

    # All three snapshots must contain exactly the pandas-selected identities.
    expected_ids = set(expected_view["_order_id"].dropna().astype(str))
    expected_lines = sorted(expected_view["_source_row_number"].tolist())
    for name in ["selected_order_ids", "order_enrichment"]:
        observed = results[name]
        actual_ids = set(observed["order_id"].dropna().astype(str))
        check(name, "order_set_match", True, expected_ids == actual_ids,
              f"Expected {len(expected_ids)} distinct orders; observed {len(actual_ids)}; compare actual selected_order_ids.csv")
    subcategories = expected_view.get("Sub-Category", pd.Series(pd.NA, index=expected_view.index, dtype="string"))
    for dimension, values in [("month", expected_view["_month"]),
                              ("sub_category", subcategories.mask(missing_mask(subcategories)))]:
        expected_sets = {scalar(key): set(group["_order_id"].dropna().astype(str))
                         for key, group in expected_view.assign(_group=values).groupby("_group", dropna=False)}
        # Groups with only blank IDs still have a group summary, but no order-set member.
        expected_sets = {key: ids for key, ids in expected_sets.items() if ids}
        actual_sets = {scalar(key): set(group["order_id"].astype(str))
                       for key, group in results["group_order_ids"].loc[
                           results["group_order_ids"]["dimension"].eq(dimension)].groupby("group_value", dropna=False)}
        check(dimension, "group_order_sets_match", True, expected_sets == actual_sets)
    joined = results["order_enrichment"]
    check("order_enrichment", "line_identity_multiset_match", True, expected_lines == sorted(joined["_source_row_number"].tolist()))
    for measure in ["sales", "profit"]:
        # Independent pandas numeric summation on the joined detail, no SQL totals reused.
        available = joined[measure].dropna()
        observed = math.fsum(available.astype(float)) if len(available) else None
        check("order_enrichment", measure, expected_metrics[measure], observed)
    check("order_enrichment", "row_count", len(expected_view), len(joined))

    # Check enrichment values against separate pandas many-to-one merges as well.
    pandas_lines = expected_view[["_source_row_number", "_region", "_order_id"]].copy()
    p = people.copy()
    r = returns.copy()
    p["_region"] = p["Region"].mask(missing_mask(p["Region"])).astype("string")
    r["_order_id"] = r["Order ID"].mask(missing_mask(r["Order ID"])).astype("string")
    pandas_lines["_order_id"] = pandas_lines["_order_id"].astype("string")
    pandas_lines = pandas_lines.merge(p[["_region", "Regional Manager"]], on="_region", how="left", validate="many_to_one")
    pandas_lines = pandas_lines.merge(r[["_order_id", "Returned"]], on="_order_id", how="left", validate="many_to_one")
    pandas_lines = pandas_lines.sort_values("_source_row_number")
    for original, alias in [("Regional Manager", "assigned_regional_manager"), ("Returned", "order_return_flag")]:
        expected = pandas_lines[original].mask(missing_mask(pandas_lines[original])).astype("string").fillna("<NULL>").tolist()
        actual = joined[alias].astype("string").fillna("<NULL>").tolist()
        check("order_enrichment", alias + "_all_rows_match", True, expected == actual)
    # Calendar groups + the undated bucket cover all lines and known amounts.
    for name in ["monthly_trends", "product_profitability"]:
        for metric in ["sales", "profit", "order_line_count"]:
            values = results[name][metric].dropna()
            observed = math.fsum(values.astype(float)) if len(values) else (0 if metric == "order_line_count" else None)
            check(name, f"coverage_{metric}", expected_metrics[metric], observed)
    return pd.DataFrame(rows)


def run_sql_analysis(sheets, project_root=ROOT, start=None, end=None, regions=None, categories=None, include_undated=True):
    for value in [start, end]:
        if value is not None:
            pd.Timestamp(value)  # Reject malformed dates before parameter binding.
    if (start is None) != (end is None):
        raise ValueError("Provide both start and end dates, or neither.")
    if start is not None and pd.Timestamp(start) > pd.Timestamp(end):
        raise ValueError("Start date must not be after end date.")
    frames, mapping = normalized_inputs(sheets)
    filters = dict(start=start, end=end, regions=regions, categories=categories, include_undated=include_undated)
    with duckdb.connect(":memory:") as connection:
        for sheet, frame in frames.items():
            connection.register(f"{sheet.lower()}_input", frame)
        connection.register("source_schema", pd.DataFrame({"profit_available": ["Profit" in sheets["Orders"]]}))
        connection.execute("""CREATE TEMP TABLE analysis_parameters AS SELECT
            CAST($start AS DATE) AS start_date, CAST($end AS DATE) AS end_date,
            CAST($regions AS VARCHAR[]) AS regions, CAST($categories AS VARCHAR[]) AS categories,
            CAST($include_undated AS BOOLEAN) AS include_undated""", filters)
        sql_dir = Path(project_root) / "sql"
        selection = (sql_dir / "00_selected_orders.sql").read_text(encoding="utf-8").strip().rstrip(";")
        connection.execute("CREATE TEMP VIEW orders_selected AS " + selection)
        results = {}
        for name, filename in QUERY_FILES.items():
            query = (sql_dir / filename).read_text(encoding="utf-8")
            try:
                results[name] = connection.execute(query).fetchdf()
            except duckdb.Error:
                if name == "order_enrichment":
                    checks = connection.execute("SELECT * FROM enrichment_key_checks").fetchdf()
                    if (checks["missing_key_rows"].gt(0) | checks["duplicate_key_rows"].gt(0)).any():
                        raise UnsafeJoinError(checks) from None
                raise
        results["join_key_checks"] = connection.execute("SELECT * FROM enrichment_key_checks").fetchdf()
        results["selected_order_ids"] = connection.execute("SELECT DISTINCT order_id FROM orders_selected WHERE order_id IS NOT NULL ORDER BY order_id").fetchdf()
        results["group_order_ids"] = connection.execute("""
            SELECT DISTINCT 'month' AS dimension, strftime(order_date, '%Y-%m') AS group_value, order_id
            FROM orders_selected WHERE order_id IS NOT NULL
            UNION ALL
            SELECT DISTINCT 'sub_category', sub_category, order_id
            FROM orders_selected WHERE order_id IS NOT NULL
            ORDER BY dimension, group_value NULLS LAST, order_id
        """).fetchdf()
        # SQL-to-SQL check uses joined SQL detail, not a pandas precomputed total.
        connection.register("enriched_detail", results["order_enrichment"])
        results["join_preservation"] = connection.execute("""
            SELECT 'before' AS stage, COUNT(*) AS order_line_count, SUM(sales) AS sales, SUM(profit) AS profit FROM orders_selected
            UNION ALL SELECT 'after', COUNT(*), SUM(sales), SUM(profit) FROM enriched_detail
        """).fetchdf()
    reconciliation = reconcile_results(results, sheets["Orders"].reset_index(drop=True), sheets["People"], sheets["Returns"], filters)
    before, after = results["join_preservation"].iloc[0], results["join_preservation"].iloc[1]
    for field in ["order_line_count", "sales", "profit"]:
        a, b = scalar(before[field]), scalar(after[field])
        equal = a is b if a is None or b is None else math.isclose(a, b, abs_tol=ABSOLUTE_TOLERANCE, rel_tol=RELATIVE_TOLERANCE)
        reconciliation.loc[len(reconciliation)] = ["join_preservation", field, a, b, "PASS" if equal else "FAIL", "SQL before/after LEFT JOIN totals"]
    return SqlAnalysis(results, reconciliation, mapping, {
        "filters": filters, "duckdb_version": duckdb.__version__,
        "metric_source": "Normalized Excel order lines; no Python aggregate is registered",
        "shared_preprocessing": "Workbook loader, row-level type validation and missing-value rules only",
        "amount_unit": "Source units; currency and profit cost basis unconfirmed",
        "monthly_rule": "Calendar completed before LAG; no-record sums remain NULL; undated bucket retained",
        "rate_rule": "Rates require complete inputs and a positive prior value; negative and zero bases retain absolute changes only",
        "returns_rule": "Order-level flag only; unmatched does not prove no return; no refunds/net sales inferred",
        "absolute_tolerance": ABSOLUTE_TOLERANCE, "relative_tolerance": RELATIVE_TOLERANCE,
        "ratio_absolute_tolerance": 1e-12,
    })
