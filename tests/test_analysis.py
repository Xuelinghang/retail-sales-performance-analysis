"""Behavioral checks for financial definitions, coverage, and safe joins."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
import pandas as pd

from retail_lab.analysis import (
    aggregate_dimension, analyze_workbook, calculate_metrics,
    duplicate_profile, prepare_orders, reconcile_dimension, relationship_report, write_outputs,
)


def sample_orders():
    return pd.DataFrame({
        "Row ID": [1, 2, 3], "Order ID": ["A", "A", "B"],
        "Order Date": ["2026-01-01", "2026-01-01", "2026-02-01"],
        "Region": ["West", "West", "East"],
        "Category": ["Furniture", "Technology", "Technology"],
        "Product ID": ["P1", "P2", "P3"],
        "Sales": [100.0, 300.0, 100.0], "Profit": [50.0, -30.0, 20.0],
    })


class MetricTests(unittest.TestCase):
    def test_margin_uses_ratio_of_totals_and_distinct_orders(self):
        metrics = calculate_metrics(prepare_orders(sample_orders()))
        self.assertEqual(metrics["sales"], 500)
        self.assertEqual(metrics["profit"], 40)
        self.assertAlmostEqual(metrics["profit_margin"], 0.08)
        self.assertNotAlmostEqual(metrics["profit_margin"], (0.5 - 0.1 + 0.2) / 3)
        self.assertEqual(metrics["order_count"], 2)
        self.assertEqual(metrics["order_line_count"], 3)

    def test_zero_denominator_is_unavailable(self):
        orders = sample_orders()
        orders["Sales"] = [100, -100, 0]
        metrics = calculate_metrics(prepare_orders(orders))
        self.assertEqual(metrics["sales"], 0)
        self.assertIsNone(metrics["profit_margin"])
        self.assertEqual(metrics["profit_margin_status"], "zero_sales")

    def test_missing_profit_is_not_zero(self):
        metrics, tables = analyze_workbook({"Orders": sample_orders().drop(columns="Profit")}, "2026-11-01")
        self.assertIsNone(metrics["profit"])
        self.assertIsNone(metrics["profit_margin"])
        self.assertEqual(metrics["profit_status"], "field_missing")
        profit_checks = tables["reconciliation"].query("check == 'profit'")
        self.assertTrue(profit_checks["status"].eq("UNAVAILABLE").all())

    def test_nonfinite_and_invalid_amounts_are_partial_not_zero(self):
        orders = sample_orders()
        orders["Sales"] = [100, "bad", float("inf")]
        metrics = calculate_metrics(prepare_orders(orders))
        self.assertEqual(metrics["sales"], 100)
        self.assertEqual(metrics["sales_status"], "partial")
        self.assertEqual(metrics["sales_unavailable_line_count"], 2)
        self.assertIsNone(metrics["profit_margin"])
        self.assertEqual(metrics["order_line_count"], 3)

    def test_all_missing_measures_are_unavailable(self):
        orders = sample_orders()
        orders["Sales"] = [None, "", "   "]
        metrics = calculate_metrics(prepare_orders(orders))
        self.assertIsNone(metrics["sales"])
        self.assertEqual(metrics["sales_status"], "no_usable_values")
        self.assertEqual(metrics["order_line_count"], 3)

    def test_missing_order_identifier_still_contributes_valid_amounts(self):
        orders = sample_orders()
        orders.loc[2, "Order ID"] = "  "
        metrics = calculate_metrics(prepare_orders(orders))
        self.assertEqual(metrics["sales"], 500)
        self.assertEqual(metrics["order_count"], 1)
        self.assertEqual(metrics["order_line_count"], 3)
        self.assertEqual(metrics["order_id_missing_line_count"], 1)


class CoverageTests(unittest.TestCase):
    def test_empty_input_has_zero_counts_without_fabricating_amounts(self):
        metrics, tables = analyze_workbook({"Orders": sample_orders().iloc[:0]}, "2026-11-01")
        self.assertEqual(metrics["order_count"], 0)
        self.assertEqual(metrics["order_line_count"], 0)
        self.assertIsNone(metrics["sales"])
        self.assertFalse(tables["reconciliation"]["status"].eq("FAIL").any())

    def test_export_keeps_unassigned_groups_and_null_measures(self):
        orders = sample_orders().drop(columns="Profit")
        orders["Order Date"] = [None, "invalid", "2026-02-01"]
        orders["Region"] = None
        metrics, tables = analyze_workbook({"Orders": orders}, "2026-11-01")
        manifest = {"source_path": "fixture.xlsx", "excel_engine": "fixture",
                    "as_of_date": "2026-11-01", "source_sha256_after": "fixture"}
        with TemporaryDirectory() as directory:
            write_outputs(directory, metrics, tables, manifest)
            monthly = pd.read_csv(Path(directory) / "monthly_summary.csv")
            regional = pd.read_csv(Path(directory) / "regional_summary.csv")
            self.assertEqual(monthly.loc[monthly["Month"].eq("__UNASSIGNED__"), "sales"].iloc[0], 400)
            self.assertEqual(regional["Region"].tolist(), ["__UNASSIGNED__"])
            self.assertEqual(regional["sales"].iloc[0], 500)
            self.assertTrue(monthly["profit"].isna().all())

    def test_invalid_dates_and_missing_groups_retain_totals_and_source(self):
        orders = sample_orders()
        orders["Order Date"] = ["2026-01-01", "invalid", 123]
        orders.loc[1, "Region"] = None
        orders.loc[2, "Category"] = "   "
        original = orders.copy(deep=True)
        metrics, tables = analyze_workbook({"Orders": orders}, "2026-11-01")
        pd.testing.assert_frame_equal(orders, original)
        for name in ["monthly_summary", "regional_summary", "category_summary"]:
            summary = tables[name]
            self.assertTrue(summary.iloc[:, 0].isna().any())
            self.assertEqual(summary["sales"].sum(), 500)
            self.assertEqual(summary["order_line_count"].sum(), 3)
        monthly = tables["monthly_summary"]
        self.assertEqual(monthly.loc[monthly["Month"].isna(), "sales"].iloc[0], 400)
        date_profile = tables["field_profile"].query("field == 'Order Date'").iloc[0]
        self.assertEqual(date_profile["invalid_date_count"], 2)
        self.assertFalse(tables["reconciliation"]["status"].eq("FAIL").any())

    def test_distinct_orders_are_not_additive_across_categories(self):
        view = prepare_orders(sample_orders())
        metrics = calculate_metrics(view)
        summary = aggregate_dimension(view, "_category", "Category")
        self.assertEqual(summary["order_count"].sum(), 3)
        self.assertEqual(metrics["order_count"], 2)
        checks = reconcile_dimension(view, summary, "_category", "Category", metrics)
        union_check = next(row for row in checks if row["check"] == "order_count_union")
        self.assertEqual(union_check["status"], "PASS")
        self.assertEqual(union_check["observed"], 2)

    def test_duplicate_definitions_are_distinct(self):
        orders = sample_orders()
        orders = pd.concat([orders, orders.iloc[[0]]], ignore_index=True)
        records, examples = duplicate_profile("Orders", orders)
        counts = {row["duplicate_type"]: row["extra_rows"] for row in records}
        self.assertEqual(counts["full_row"], 1)
        self.assertEqual(counts["order_id"], 2)
        self.assertEqual(counts["order_line_id"], 1)
        self.assertEqual(len(orders), 4)


class RelationshipTests(unittest.TestCase):
    def test_duplicate_dimension_key_blocks_join_without_inflation(self):
        orders = sample_orders()
        people = pd.DataFrame({"Region": ["West", "West", "East"]})
        report = relationship_report({"Orders": orders, "People": people})
        result = report.loc[report["right_sheet"].eq("People")].iloc[0]
        self.assertEqual(result["status"], "BLOCKED_UNSAFE_RIGHT_KEY")
        self.assertTrue(pd.isna(result["joined_row_count"]))
        self.assertEqual(calculate_metrics(prepare_orders(orders))["sales"], 500)

    def test_unmatched_returns_keep_all_order_lines(self):
        returns = pd.DataFrame({"Order ID": ["A"], "Returned": ["Yes"]})
        report = relationship_report({"Orders": sample_orders(), "Returns": returns})
        result = report.loc[report["right_sheet"].eq("Returns")].iloc[0]
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["unmatched_order_lines"], 1)
        self.assertEqual(result["joined_row_count"], 3)
        self.assertTrue(result["sales_preserved"])
        self.assertTrue(result["profit_preserved"])


if __name__ == "__main__":
    unittest.main()
