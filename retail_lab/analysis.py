"""Read-only workbook profiling, line-item metrics, and reconciliation."""

from hashlib import sha256
import json
import math
from numbers import Number
from pathlib import Path

import pandas as pd


FIELD_MEANINGS = {
    "Row ID": "Source line-item identifier",
    "Order ID": "Order identifier; one order can contain multiple rows",
    "Order Date": "Date the order was placed",
    "Ship Date": "Shipment date, not delivery date",
    "Ship Mode": "Shipping service class",
    "Customer ID": "Customer identifier",
    "Customer Name": "Customer name",
    "Segment": "Customer segment",
    "Country/Region": "Country or geographic jurisdiction",
    "City": "City",
    "State/Province": "State or province",
    "Postal Code": "Postal identifier; not a numeric measure",
    "Region": "Sales region; join key for People",
    "Product ID": "Product identifier",
    "Category": "Top-level product category",
    "Sub-Category": "Product subcategory",
    "Product Name": "Product name",
    "Sales": "Line sales amount; currency and tax treatment unconfirmed",
    "Quantity": "Number of product units on the line item",
    "Discount": "Discount rate; 0.2 represents 20%",
    "Profit": "Line profit amount; cost definition unconfirmed",
    "Regional Manager": "Manager responsible for the sales region",
    "Returned": "Order-level return flag; observed value is Yes",
}
SHEET_GRAINS = {
    "Orders": "One product sales line item within an order",
    "People": "One sales region and its regional manager",
    "Returns": "One order-level return flag; not a returned product line",
}
NUMERIC_FIELDS = {"Row ID", "Sales", "Profit", "Quantity", "Discount"}
ABSOLUTE_TOLERANCE = 1e-6
RELATIVE_TOLERANCE = 1e-12


def file_hash(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def detect_excel_engine(path):
    """Reuse the signature check established in the data-overview notebook."""
    with Path(path).open("rb") as source_file:
        signature = source_file.read(8)
    if signature == bytes.fromhex("D0CF11E0A1B11AE1"):
        return "xlrd"
    if signature.startswith(b"PK"):
        return "openpyxl"
    raise ValueError("Unrecognized Excel file signature.")


def load_workbook(path):
    engine = detect_excel_engine(path)
    return pd.read_excel(path, sheet_name=None, engine=engine)


def missing_mask(series):
    """Treat whitespace-only strings as missing in derived analysis only."""
    blanks = series.astype("string").str.strip().eq("").fillna(False)
    return series.isna() | blanks


def finite_numbers(series):
    numbers = pd.to_numeric(series.mask(missing_mask(series)), errors="coerce")
    return numbers.mask(numbers.isin([math.inf, -math.inf]))


def parse_dates(series):
    # Typed Excel dates are already decoded by the reader. Untyped numeric
    # values must not silently become nanosecond dates near 1970.
    # Empty Arrow-backed string Series preserve their string dtype after map.
    # Force a boolean mask so empty filtered selections remain valid.
    numeric_values = series.map(lambda value: isinstance(value, Number)).astype(bool)
    return pd.to_datetime(
        series.mask(missing_mask(series) | numeric_values),
        errors="coerce",
        format="mixed",
    )


def sum_known(series):
    values = series.dropna()
    return math.fsum(float(value) for value in values) if len(values) else None


def prepare_orders(orders):
    """Build a derived view without deleting, imputing, or deduplicating rows."""
    required = {"Sales", "Order ID"}
    if missing := required - set(orders.columns):
        raise ValueError(f"Orders is missing required metric fields: {sorted(missing)}")
    view = orders.copy(deep=True)
    view["_sales"] = finite_numbers(orders["Sales"])
    if "Profit" in orders:
        view["_profit"] = finite_numbers(orders["Profit"])
    view["_order_id"] = orders["Order ID"].mask(missing_mask(orders["Order ID"]))
    order_dates = parse_dates(orders.get("Order Date", pd.Series(pd.NaT, index=orders.index)))
    view["_month"] = order_dates.dt.strftime("%Y-%m").astype("string")
    for column in ["Region", "Category"]:
        original = orders.get(column, pd.Series(pd.NA, index=orders.index, dtype="string"))
        view[f"_{column.lower()}"] = original.mask(missing_mask(original))
    return view


def calculate_metrics(view):
    sales = sum_known(view["_sales"])
    profit_available = "_profit" in view
    profit = sum_known(view["_profit"]) if profit_available else None
    sales_unavailable = int(view["_sales"].isna().sum())
    profit_unavailable = int(view["_profit"].isna().sum()) if profit_available else len(view)
    if not profit_available:
        margin_status = "profit_field_missing"
    elif sales is None or profit is None:
        margin_status = "no_usable_measures"
    elif sales_unavailable or profit_unavailable:
        margin_status = "incomplete_measures"
    elif sales == 0:
        margin_status = "zero_sales"
    else:
        margin_status = "available"
    return {
        "sales": sales,
        "profit": profit,
        "profit_margin": profit / sales if margin_status == "available" else None,
        "order_count": int(view["_order_id"].nunique(dropna=True)),
        "order_line_count": len(view),
        "sales_status": "no_usable_values" if sales is None else ("partial" if sales_unavailable else "complete"),
        "profit_status": "field_missing" if not profit_available else (
            "no_usable_values" if profit is None else ("partial" if profit_unavailable else "complete")
        ),
        "profit_margin_status": margin_status,
        "sales_unavailable_line_count": sales_unavailable,
        "profit_unavailable_line_count": profit_unavailable,
        "order_id_missing_line_count": int(view["_order_id"].isna().sum()),
    }


def aggregate_dimension(view, dimension, label):
    rows = []
    for group_value, group in view.groupby(dimension, dropna=False, sort=True):
        rows.append({label: group_value, **calculate_metrics(group)})
    columns = [label, *calculate_metrics(view)]
    return pd.DataFrame(rows, columns=columns)


def reconcile_dimension(view, summary, dimension, label, totals):
    """Reconcile additive measures and distinct order unions separately."""
    rows = []
    for metric in ["sales", "profit", "order_line_count"]:
        actual = int(summary[metric].sum()) if metric == "order_line_count" else sum_known(summary[metric])
        expected = totals[metric]
        status = "UNAVAILABLE" if expected is None else (
            "PASS" if actual is not None and math.isclose(
                actual, expected, abs_tol=ABSOLUTE_TOLERANCE, rel_tol=RELATIVE_TOLERANCE
            ) else "FAIL"
        )
        rows.append({"dimension": label, "check": metric, "expected": expected,
                     "observed": actual, "difference": None if expected is None or actual is None else actual - expected,
                     "status": status, "note": "Includes the unassigned group; amounts retain source precision."})
    order_union = set()
    for _, group in view.groupby(dimension, dropna=False):
        order_union.update(group["_order_id"].dropna().tolist())
    rows.append({"dimension": label, "check": "order_count_union", "expected": totals["order_count"],
                 "observed": len(order_union), "difference": len(order_union) - totals["order_count"],
                 "status": "PASS" if len(order_union) == totals["order_count"] else "FAIL",
                 "note": "Distinct union across groups; do not sum group-level order counts."})
    rows.append({"dimension": label, "check": "order_count_sum_information", "expected": totals["order_count"],
                 "observed": int(summary["order_count"].sum()), "difference": int(summary["order_count"].sum()) - totals["order_count"],
                 "status": "INFO", "note": "An order may appear in multiple groups; the sum need not equal the total."})
    return rows


def duplicate_profile(sheet_name, frame):
    definitions = [("full_row", list(frame.columns), "Exact duplicate source records, including identifiers.")]
    if "Order ID" in frame:
        meaning = "Expected at line-item grain; repeated orders are not automatically errors." if sheet_name == "Orders" else "Expected unique at the declared order-level grain."
        definitions.append(("order_id", ["Order ID"], meaning))
    if "Row ID" in frame:
        definitions.extend([
            ("order_line_id", ["Row ID"], "Expected unique source line identifier."),
            ("content_excluding_row_id", [column for column in frame if column != "Row ID"], "Potential duplicates requiring review; retained in metrics."),
        ])
    if {"Order ID", "Product ID"}.issubset(frame.columns):
        definitions.append(("order_product", ["Order ID", "Product ID"], "A candidate key only; repeated products may be split line items."))
    if sheet_name == "People" and "Region" in frame:
        definitions.append(("region_key", ["Region"], "Expected unique on the right side of a many-to-one join."))
    summaries, examples = [], []
    for duplicate_type, columns, meaning in definitions:
        missing_keys = pd.Series(False, index=frame.index)
        if duplicate_type not in {"full_row", "content_excluding_row_id"}:
            for column in columns:
                missing_keys |= missing_mask(frame[column])
        keys = frame.loc[~missing_keys, columns]
        extra = keys.duplicated(keep="first")
        affected = keys.duplicated(keep=False)
        summaries.append({
            "sheet": sheet_name, "duplicate_type": duplicate_type,
            "key_fields": " | ".join(columns), "extra_rows": int(extra.sum()),
            "affected_rows": int(affected.sum()),
            "repeated_key_groups": len(keys.loc[affected].drop_duplicates()),
            "missing_key_rows": int(missing_keys.sum()), "interpretation": meaning,
        })
        for position in keys.index[affected][:10]:
            identifiers = {column: str(frame.loc[position, column]) for column in ["Row ID", "Order ID", "Product ID", "Region"] if column in frame}
            examples.append({"sheet": sheet_name, "duplicate_type": duplicate_type,
                             "source_excel_row": int(frame.index.get_loc(position)) + 2,
                             "identifiers": json.dumps(identifiers),
                             "scope": "First 10 affected rows per duplicate type; not an exhaustive export."})
    return summaries, examples


def profile_workbook(sheets, as_of_date):
    sheet_rows, field_rows, duplicates, duplicate_examples, checks = [], [], [], [], []

    def add_check(sheet, field, check, mask, note, severity="REVIEW"):
        checks.append({"sheet": sheet, "field": field, "check": check,
                       "count": int(mask.sum()), "row_denominator": len(mask),
                       "rate": float(mask.mean()) if len(mask) else None,
                       "severity_when_present": severity, "interpretation": note})

    for sheet_name, frame in sheets.items():
        date_fields = [column for column in frame if "date" in str(column).lower() or pd.api.types.is_datetime64_any_dtype(frame[column])]
        sheet_rows.append({
            "sheet": sheet_name, "data_rows": len(frame), "columns": len(frame.columns),
            "grain": SHEET_GRAINS.get(sheet_name, "Unknown; requires review"),
            "date_fields": " | ".join(date_fields) or "None",
            "null_cells": int(frame.isna().sum().sum()),
            "blank_string_cells": int(sum((missing_mask(frame[column]) & frame[column].notna()).sum() for column in frame)),
            "exact_duplicate_extra_rows": int(frame.duplicated().sum()),
        })
        duplicate_rows, example_rows = duplicate_profile(sheet_name, frame)
        duplicates.extend(duplicate_rows)
        duplicate_examples.extend(example_rows)
        for column in frame:
            series = frame[column]
            missing = missing_mask(series)
            dates = parse_dates(series) if column in date_fields else None
            numbers = finite_numbers(series) if column in NUMERIC_FIELDS else None
            invalid_date = dates.isna() & ~missing if dates is not None else None
            invalid_numeric = numbers.isna() & ~missing if numbers is not None else None
            field_rows.append({
                "sheet": sheet_name, "field": column, "read_dtype": str(series.dtype),
                "meaning": FIELD_MEANINGS.get(column, "Not documented; requires review"),
                "null_count": int(series.isna().sum()),
                "blank_string_count": int((missing & series.notna()).sum()),
                "missing_or_blank_rate": float(missing.mean()) if len(series) else None,
                "distinct_nonmissing_values": int(series.loc[~missing].nunique()),
                "date_min": dates.min().isoformat() if dates is not None and dates.notna().any() else None,
                "date_max": dates.max().isoformat() if dates is not None and dates.notna().any() else None,
                "invalid_date_count": int(invalid_date.sum()) if invalid_date is not None else None,
                "invalid_numeric_count": int(invalid_numeric.sum()) if invalid_numeric is not None else None,
            })
            add_check(sheet_name, column, "missing_or_blank", missing, "Retained. Missing measures are not imputed as zero; missing grouping values stay in an unassigned group.")
            if dates is not None:
                add_check(sheet_name, column, "invalid_date", invalid_date, "Retained. Invalid Order Date values enter the unassigned monthly group.")
                add_check(sheet_name, column, "after_reference_date", dates.gt(as_of_date), "Retained sample dates; this reference date is not a reporting filter.")
            if numbers is not None:
                add_check(sheet_name, column, "invalid_or_nonfinite_numeric", invalid_numeric, "Retained in source and line count; excluded only from this measure's sum. Incomplete inputs make margin unavailable.")
                if column in {"Sales", "Profit"} and numbers.notna().any():
                    lower_quartile, upper_quartile = numbers.quantile([0.25, 0.75])
                    spread = upper_quartile - lower_quartile
                    low, high = lower_quartile - 1.5 * spread, upper_quartile + 1.5 * spread
                    add_check(sheet_name, column, "iqr_outlier", numbers.lt(low) | numbers.gt(high), f"Review flag only; retained in totals. 1.5-IQR bounds: {low:.8g} to {high:.8g}.")
        if sheet_name == "Orders":
            for column in ["Sales", "Profit", "Quantity", "Discount"]:
                if column not in frame:
                    add_check(sheet_name, column, "field_missing", pd.Series(True, index=frame.index), "Field unavailable; do not fabricate the measure or treat it as zero.")
                    continue
                numbers = finite_numbers(frame[column])
                if column in {"Sales", "Profit"}:
                    add_check(sheet_name, column, "negative_value", numbers.lt(0), "Retained. Negative profit can indicate a loss; investigate negative sales before assuming an error.")
                if column == "Sales":
                    add_check(sheet_name, column, "zero_value", numbers.eq(0), "Retained. Aggregate zero sales produces an undefined profit margin.")
                if column == "Quantity":
                    add_check(sheet_name, column, "nonpositive_value", numbers.le(0), "Flag for review; no row deletion.")
                if column == "Discount":
                    add_check(sheet_name, column, "outside_unit_interval", numbers.notna() & ~numbers.between(0, 1), "Flag for review; no row deletion.")
            if {"Order Date", "Ship Date"}.issubset(frame.columns):
                add_check(sheet_name, "Ship Date", "ship_before_order", parse_dates(frame["Ship Date"]).lt(parse_dates(frame["Order Date"])), "Compare valid dates only; retain flagged rows.")
    return {
        "sheet_summary": pd.DataFrame(sheet_rows), "field_profile": pd.DataFrame(field_rows),
        "duplicate_summary": pd.DataFrame(duplicates),
        "duplicate_examples": pd.DataFrame(duplicate_examples, columns=["sheet", "duplicate_type", "source_excel_row", "identifiers", "scope"]),
        "quality_checks": pd.DataFrame(checks),
    }


def relationship_report(sheets):
    """Trial joins are guarded and never supply the metric fact table."""
    orders = sheets["Orders"]
    rows = []
    for right_name, key in [("People", "Region"), ("Returns", "Order ID")]:
        result = {"left_sheet": "Orders", "right_sheet": right_name, "key": key,
                  "expected_cardinality": "many_to_one", "status": "UNAVAILABLE",
                  "right_duplicate_key_extra_rows": None, "right_missing_key_rows": None,
                  "unmatched_order_lines": None, "right_keys_absent_in_orders": None,
                  "joined_row_count": None, "row_count_preserved": None,
                  "sales_preserved": None, "profit_preserved": None,
                  "note": "Metrics always use Orders alone. Returns has order-level flags, not return quantities or amounts."}
        if right_name not in sheets or key not in orders or key not in sheets[right_name]:
            rows.append(result)
            continue
        right = sheets[right_name]
        duplicate_count = int(right[key].duplicated().sum())
        missing_count = int(missing_mask(right[key]).sum())
        result.update(right_duplicate_key_extra_rows=duplicate_count, right_missing_key_rows=missing_count)
        result["right_keys_absent_in_orders"] = len(set(right.loc[~missing_mask(right[key]), key]) - set(orders.loc[~missing_mask(orders[key]), key]))
        if duplicate_count or missing_count:
            result["status"] = "BLOCKED_UNSAFE_RIGHT_KEY"
            rows.append(result)
            continue
        joined = orders.merge(right[[key]], on=key, how="left", validate="many_to_one", indicator=True)
        result.update(
            status="PASS", unmatched_order_lines=int(joined["_merge"].eq("left_only").sum()),
            joined_row_count=len(joined), row_count_preserved=len(joined) == len(orders),
        )
        for column in ["Sales", "Profit"]:
            if column in orders:
                before = sum_known(finite_numbers(orders[column]))
                after = sum_known(finite_numbers(joined[column]))
                result[f"{column.lower()}_preserved"] = before == after
        if not result["row_count_preserved"] or result["sales_preserved"] is False or result["profit_preserved"] is False:
            result["status"] = "FAIL"
        rows.append(result)
    return pd.DataFrame(rows)


def analyze_workbook(sheets, as_of_date):
    if "Orders" not in sheets:
        raise ValueError("The workbook must contain an Orders sheet.")
    view = prepare_orders(sheets["Orders"])
    metrics = calculate_metrics(view)
    tables = profile_workbook(sheets, pd.Timestamp(as_of_date))
    reconciliation = []
    for output_name, dimension, label in [
        ("monthly_summary", "_month", "Month"),
        ("regional_summary", "_region", "Region"),
        ("category_summary", "_category", "Category"),
    ]:
        summary = aggregate_dimension(view, dimension, label)
        reconciliation.extend(reconcile_dimension(view, summary, dimension, label, metrics))
        tables[output_name] = summary
    tables["reconciliation"] = pd.DataFrame(reconciliation)
    tables["relationships"] = relationship_report(sheets)
    return metrics, tables


def write_outputs(output_dir, metrics, tables, manifest):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, frame in tables.items():
        exported = frame.copy()
        if name.endswith("_summary") and name in {"monthly_summary", "regional_summary", "category_summary"}:
            group_column = exported.columns[0]
            exported[group_column] = exported[group_column].astype("string").fillna("__UNASSIGNED__")
        exported.to_csv(output_dir / f"{name}.csv", index=False, encoding="utf-8")
    pd.DataFrame([metrics]).to_csv(output_dir / "metrics.csv", index=False, encoding="utf-8")
    for name, value in [("metrics", metrics), ("run_manifest", manifest)]:
        (output_dir / f"{name}.json").write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    lines = [
        "# Phase 1 Data Quality and Metrics", "",
        f"Source: `{manifest['source_path']}`. Engine: `{manifest['excel_engine']}`.",
        f"Inspection reference date: {manifest['as_of_date']}. No date filter is applied.", "",
        "## Base Metrics", "", "| Metric | Value |", "| --- | ---: |",
    ]
    for name in ["sales", "profit", "profit_margin", "order_count", "order_line_count"]:
        value = metrics[name]
        if value is None:
            rendered = "Unavailable"
        elif name == "profit_margin":
            rendered = f"{value:.6%}"
        elif name in {"sales", "profit"}:
            rendered = f"{value:,.4f}"
        else:
            rendered = f"{value:,}"
        lines.append(f"| {name} | {rendered} |")
    lines.extend(["", "Amounts above are displayed to four decimal places. CSV and JSON artifacts retain calculation precision."])
    lines.extend(["", "## Workbook Grain", ""])
    for row in tables["sheet_summary"].to_dict("records"):
        lines.append(f"- {row['sheet']}: {row['data_rows']:,} rows, {row['columns']} fields. {row['grain']}.")
    lines.extend(["", "## Review Findings", ""])
    for row in tables["duplicate_summary"].to_dict("records"):
        lines.append(f"- {row['sheet']} / {row['duplicate_type']}: {row['extra_rows']:,} extra rows; {row['affected_rows']:,} affected rows. {row['interpretation']}")
    for row in tables["quality_checks"].to_dict("records"):
        if row["count"]:
            lines.append(f"- {row['sheet']}.{row['field']} / {row['check']}: {row['count']:,} of {row['row_denominator']:,} rows ({row['rate']:.2%}). {row['interpretation']}")
    passed = int(tables["reconciliation"]["status"].eq("PASS").sum())
    lines.extend([
        "", "## Reconciliation and Relationships", "",
        f"Reconciliation checks passed: {passed}. See `reconciliation.csv` for differences and availability.",
        "Sales, Profit, and line counts are additive. Order Count uses the distinct union across groups; group order counts are not necessarily additive.",
        "Profit Margin is SUM(Profit) / SUM(Sales), never the mean of row or group margins.",
        "Metrics are calculated directly from Orders before any enrichment. Trial left joins use validate='many_to_one'; unsafe right-side keys block trial joins without changing the metric source.",
        "Orders.Region -> People.Region maps regions to managers. Orders.Order ID -> Returns.Order ID attaches an order-level return flag. Orders absent from Returns are expected; missing flags do not prove the absence of returns without a completeness guarantee.",
        "", "## Data Handling and Limits", "",
        "- No rows are automatically deleted, deduplicated, clipped, or imputed. Review flags and negative profit remain in totals.",
        "- Null and whitespace-only grouping values, and missing or invalid order dates, appear in an __UNASSIGNED__ group so their valid amounts and line counts reconcile.",
        "- Non-numeric, non-finite, and missing amounts are excluded only from the affected sum, which is labeled partial or unavailable. Missing measures are not zero. Margin is unavailable when either measure is incomplete or total Sales is zero.",
        "- Missing order identifiers are excluded from distinct Order Count but retained in line count and valid amount totals.",
        "- IQR flags identify unusual values for review, not invalid records. Reconciliation proves consistent arithmetic and coverage, not business accuracy.",
        "- Source dates include future sample records. Currency, tax treatment, profit cost basis, and return-list completeness remain unconfirmed. Amount totals are in source units, not confirmed USD.",
        f"- Raw source SHA-256 preserved: `{manifest['source_sha256_after']}`.", "",
    ])
    (output_dir / "data_quality_summary.md").write_text("\n".join(lines), encoding="utf-8")
