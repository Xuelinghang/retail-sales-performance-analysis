"""Period comparison definitions, contribution coverage, and UI integration."""

from datetime import date
from io import BytesIO
import json
from pathlib import Path
import unittest

import pandas as pd
from streamlit.testing.v1 import AppTest

from retail_lab.dashboard import load_verified_source
from retail_lab.drivers import (
    build_driver_model, contribution_figure, driver_downloads,
    largest_contributors, month_plan,
)
from test_dashboard import CAPTURED_APP

ROOT = Path(__file__).resolve().parents[1]


def orders_fixture():
    return pd.DataFrame({
        "Row ID": [1, 2, 3, 4], "Order ID": ["A", "B", "C", "D"],
        "Order Date": ["2026-01-01", "2026-01-15", "2026-02-01", "2026-02-28"],
        "Region": ["West", "East", "West", "South"], "Category": ["Furniture"] * 4,
        "Sub-Category": ["Tables", "Chairs", "Tables", "Phones"],
        "Sales": [100.0, 100.0, 120.0, 180.0], "Profit": [5.0, 15.0, 10.0, 50.0],
    })


def metric_row(model, measure):
    return model.comparison.loc[model.comparison["metric"].eq(measure)].iloc[0]


class DriverCalculationTests(unittest.TestCase):
    def test_normal_changes_and_percentage_point_definition(self):
        orders = orders_fixture()
        original = orders.copy(deep=True)
        model = build_driver_model(orders, "2026-01", "2026-02")
        sales, profit, margin = [metric_row(model, measure) for measure in ["sales", "profit", "profit_margin"]]
        self.assertEqual(sales["absolute_change"], 100)
        self.assertEqual(sales["change_rate"], 0.5)
        self.assertEqual(profit["absolute_change"], 40)
        self.assertEqual(profit["change_rate"], 2)
        self.assertAlmostEqual(margin["margin_change_pp"], 10)
        self.assertTrue(pd.isna(margin["change_rate"]))
        self.assertEqual(model.reconciliation["status"].value_counts().to_dict(), {"PASS": 12})
        pd.testing.assert_frame_equal(orders, original)

    def test_zero_baseline_has_no_change_rate(self):
        orders = orders_fixture()
        orders["Sales"] = [0, 0, 120, 180]
        orders["Profit"] = [0, 0, 10, 50]
        model = build_driver_model(orders, "2026-01", "2026-02")
        for measure in ["sales", "profit"]:
            row = metric_row(model, measure)
            self.assertTrue(pd.isna(row["change_rate"]))
            self.assertEqual(row["change_rate_status"], "zero_baseline")
        self.assertEqual(metric_row(model, "sales")["absolute_change"], 300)
        self.assertTrue(pd.isna(metric_row(model, "profit_margin")["margin_change_pp"]))

    def test_loss_to_profit_uses_absolute_change_not_misleading_rate(self):
        orders = orders_fixture()
        orders["Profit"] = [-10, -10, 10, 50]
        model = build_driver_model(orders, "2026-01", "2026-02")
        row = metric_row(model, "profit")
        self.assertEqual(row["absolute_change"], 80)
        self.assertEqual(row["change_rate_status"], "negative_baseline")
        self.assertTrue(pd.isna(row["change_rate"]))
        self.assertIn("loss to profit", " ".join(model.summary))

    def test_one_period_only_groups_receive_zero_on_absent_side(self):
        model = build_driver_model(orders_fixture(), "2026-01", "2026-02")
        table = model.contributions["subcategory"].set_index("Sub-Category")
        self.assertEqual(table.loc["Chairs", "comparison_sales"], 0)
        self.assertEqual(table.loc["Chairs", "sales_change"], -100)
        self.assertEqual(table.loc["Phones", "baseline_sales"], 0)
        self.assertEqual(table.loc["Phones", "sales_change"], 180)
        self.assertEqual(table["sales_change"].sum(), 100)
        self.assertEqual(table["profit_change"].sum(), 40)
        extremes = largest_contributors(model.contributions["subcategory"], "sales")
        self.assertEqual(extremes["positive"]["groups"], ["Phones"])
        self.assertEqual(extremes["negative"]["groups"], ["Chairs"])

    def test_missing_groups_are_retained_and_exported(self):
        orders = orders_fixture()
        orders.loc[[0, 2], "Region"] = None
        orders.loc[[1, 3], "Sub-Category"] = None
        model = build_driver_model(orders, "2026-01", "2026-02")
        for table in model.contributions.values():
            self.assertTrue(table["is_missing_group"].any())
            self.assertEqual(table["sales_change"].sum(), 100)
            self.assertEqual(table["profit_change"].sum(), 40)
        exported = pd.read_csv(BytesIO(driver_downloads(model)["region_contributions"]))
        self.assertIn("__UNASSIGNED__", exported["Region"].tolist())

    def test_existing_unknown_amount_is_not_zero_filled(self):
        orders = orders_fixture()
        orders.loc[1, "Profit"] = None
        model = build_driver_model(orders, "2026-01", "2026-02")
        chairs = model.contributions["subcategory"].set_index("Sub-Category").loc["Chairs"]
        self.assertTrue(pd.isna(chairs["baseline_profit"]))
        self.assertTrue(pd.isna(chairs["profit_change"]))
        self.assertTrue(pd.isna(metric_row(model, "profit")["absolute_change"]))
        self.assertTrue(model.reconciliation.loc[model.reconciliation["measure"].eq("sales"), "status"].eq("PASS").all())
        self.assertFalse(model.reconciliation["status"].eq("FAIL").any())

    def test_missing_profit_preserves_sales_comparison(self):
        model = build_driver_model(orders_fixture().drop(columns="Profit"), "2026-01", "2026-02")
        self.assertEqual(metric_row(model, "sales")["absolute_change"], 100)
        self.assertTrue(pd.isna(metric_row(model, "profit")["absolute_change"]))
        self.assertIsNone(contribution_figure(model, "region", "profit"))

    def test_empty_period_is_zero_recorded_slice_but_empty_selection_is_disclosed(self):
        model = build_driver_model(orders_fixture(), "2026-01", "2026-02", regions=["East"])
        sales = metric_row(model, "sales")
        self.assertEqual(sales["comparison_value"], 0)
        self.assertEqual(sales["absolute_change"], -100)
        self.assertEqual(model.reconciliation["status"].value_counts().to_dict(), {"PASS": 12})
        empty = build_driver_model(orders_fixture(), "2026-01", "2026-02", regions=[])
        self.assertEqual(metric_row(empty, "sales")["absolute_change"], 0)
        self.assertIn("Neither selected month", empty.summary[0])
        self.assertFalse(empty.reconciliation["status"].eq("FAIL").any())

    def test_month_defaults_exclude_truncated_edges_without_claiming_completeness(self):
        orders = orders_fixture()
        march = orders.iloc[[3]].copy()
        march["Order Date"] = "2026-03-20"
        orders = pd.concat([orders, march], ignore_index=True)
        self.assertEqual(month_plan(orders)["defaults"], ("2026-01", "2026-02"))
        model = build_driver_model(orders, "2026-01", "2026-02", date(2026, 1, 10), date(2026, 2, 20))
        self.assertEqual(model.periods["effective_start"].tolist(), ["2026-01-10", "2026-02-01"])
        self.assertEqual(model.periods["effective_end_inclusive"].tolist(), ["2026-01-31", "2026-02-20"])
        self.assertFalse(model.periods["full_calendar_window"].any())
        self.assertFalse(model.context["data_completeness_confirmed"])

    def test_charts_and_exports_reconcile_to_overall_deltas(self):
        model = build_driver_model(orders_fixture(), "2026-01", "2026-02")
        downloads = driver_downloads(model)
        context = json.loads(downloads["drivers_context"])
        self.assertEqual(context["baseline_month"], "2026-01")
        for dimension in ["region", "subcategory"]:
            exported = pd.read_csv(BytesIO(downloads[f"{dimension}_contributions"]))
            for measure, expected in [("sales", 100), ("profit", 40)]:
                self.assertEqual(exported[f"{measure}_change"].sum(), expected)
                figure = contribution_figure(model, dimension, measure)
                self.assertEqual(sum(figure.data[0].x), expected)
                self.assertEqual(list(figure.data[0].x), sorted(figure.data[0].x))

    def test_same_month_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "different months"):
            build_driver_model(orders_fixture(), "2026-01", "2026-01")


class DriverWidgetTests(unittest.TestCase):
    def test_month_selectors_filters_downloads_and_overview_are_consistent(self):
        app = AppTest.from_string(CAPTURED_APP, default_timeout=30).run()
        self.assertFalse(app.exception)
        self.assertEqual(app.selectbox(key="drivers_baseline_month").value, "2026-10")
        self.assertEqual(app.selectbox(key="drivers_comparison_month").value, "2026-11")
        original_overview = app.session_state["test_downloads"]["download_order_lines"]
        app.selectbox(key="drivers_baseline_month").set_value("2026-09")
        app.selectbox(key="drivers_comparison_month").set_value("2026-10")
        app.run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state["test_downloads"]["download_order_lines"], original_overview)
        app.multiselect(key="regions").set_value(["West"])
        app.multiselect(key="categories").set_value(["Furniture"])
        app.run()
        self.assertFalse(app.exception)
        payloads = app.session_state["test_downloads"]
        context = json.loads(payloads["download_drivers_context"])
        self.assertEqual(context["regions"], ["West"])
        self.assertEqual(context["categories"], ["Furniture"])
        comparison = pd.read_csv(BytesIO(payloads["download_period_comparison"]))
        for dimension in ["region", "subcategory"]:
            contribution = pd.read_csv(BytesIO(payloads[f"download_{dimension}_contributions"]))
            for measure in ["sales", "profit"]:
                expected = comparison.loc[comparison["metric"].eq(measure), "absolute_change"].iloc[0]
                self.assertAlmostEqual(contribution[f"{measure}_change"].sum(), expected)
        for chart in app.get("plotly_chart"):
            spec = json.loads(chart.proto.spec)
            meta = spec.get("layout", {}).get("meta", {})
            if meta.get("scope") == "performance_drivers":
                expected = comparison.loc[comparison["metric"].eq(meta["measure"]), "absolute_change"].iloc[0]
                self.assertAlmostEqual(sum(spec["data"][0]["x"]), expected)
        self.assertTrue(any("Month completeness is unverified" in element.value for element in app.warning))
        app.download_button(key="download_period_comparison").click().run()
        self.assertFalse(app.exception)

    def test_matching_month_selection_has_prompt_without_breaking_overview(self):
        app = AppTest.from_string(CAPTURED_APP, default_timeout=30).run()
        app.selectbox(key="drivers_comparison_month").set_value("2026-10").run()
        self.assertFalse(app.exception)
        self.assertTrue(any("two different months" in element.value for element in app.info))
        self.assertEqual(app.metric[0].value, "2,326,534.35")


if __name__ == "__main__":
    unittest.main()
