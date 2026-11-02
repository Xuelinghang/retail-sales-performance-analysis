"""Explicit sensitivity examples, scope consistency, and assumption reset."""

from datetime import date
from io import BytesIO
import json
import math
from pathlib import Path
import unittest

import pandas as pd
from streamlit.testing.v1 import AppTest

from retail_lab.dashboard import build_dashboard_model, load_verified_source
from retail_lab.scenario import build_scenario_model, calculate_scenario, scenario_downloads, scenario_figure
from test_dashboard import CAPTURED_APP

ROOT = Path(__file__).resolve().parents[1]


def fixture_orders(sales=1000, profit=200):
    return pd.DataFrame({"Row ID": [1], "Order ID": ["A"], "Order Date": ["2026-01-15"],
                         "Region": ["West"], "Category": ["Technology"], "Sub-Category": ["Phones"],
                         "Sales": [sales], "Profit": [profit]})


def fixture_app(orders):
    source = CAPTURED_APP.replace("from dashboard.app import main", "from dashboard.app import render_dashboard\nimport pandas as pd")
    source = source.replace("    main()", f"    render_dashboard(pd.DataFrame({orders.to_dict('list')!r}))")
    return AppTest.from_string(source, default_timeout=30).run()


def receipt(app):
    return json.loads(app.session_state["test_downloads"]["download_scenario_json"])


class ScenarioCalculationTests(unittest.TestCase):
    def test_zero_assumptions_restore_baseline(self):
        model = calculate_scenario(1000, 200)
        self.assertTrue(model.applicable)
        self.assertEqual(model.baseline, model.scenario)
        self.assertEqual(model.scenario["implied_cost_proxy"], 800)
        self.assertTrue(all(value == 0 for value in model.changes.values()))

    def test_price_only_ten_percent(self):
        model = calculate_scenario(1000, 200, price_change=0.1)
        self.assertEqual(model.scenario["sales"], 1100)
        self.assertEqual(model.scenario["implied_cost_proxy"], 800)
        self.assertEqual(model.scenario["profit"], 300)
        self.assertAlmostEqual(model.scenario["profit_margin"], 300 / 1100)

    def test_volume_only_ten_percent(self):
        model = calculate_scenario(1000, 200, volume_change=0.1)
        self.assertEqual(model.scenario["sales"], 1100)
        self.assertEqual(model.scenario["implied_cost_proxy"], 880)
        self.assertEqual(model.scenario["profit"], 220)
        self.assertEqual(model.scenario["profit_margin"], 0.2)

    def test_cost_only_ten_percent(self):
        model = calculate_scenario(1000, 200, cost_change=0.1)
        self.assertEqual(model.scenario["sales"], 1000)
        self.assertEqual(model.scenario["implied_cost_proxy"], 880)
        self.assertEqual(model.scenario["profit"], 120)
        self.assertAlmostEqual(model.changes["profit_margin_pp"], -8)

    def test_combined_changes_follow_multiplicative_formula(self):
        model = calculate_scenario(1000, 200, 0.1, 0.1, 0.1)
        self.assertEqual(model.scenario["sales"], 1210)
        self.assertEqual(model.scenario["implied_cost_proxy"], 968)
        self.assertEqual(model.scenario["profit"], 242)

    def test_zero_scenario_sales_has_null_margin(self):
        model = calculate_scenario(1000, 200, volume_change=-1)
        self.assertTrue(model.applicable)
        self.assertEqual(model.scenario["sales"], 0)
        self.assertEqual(model.scenario["implied_cost_proxy"], 0)
        self.assertEqual(model.scenario["profit"], 0)
        self.assertIsNone(model.scenario["profit_margin"])
        self.assertIsNone(model.changes["profit_margin_pp"])
        self.assertIn("zero", model.explanation)

    def test_invalid_baselines_are_not_modeled(self):
        for sales, profit in [(0, 0), (-100, -20), (100, 101), (None, 20), (100, None), (float("inf"), 20)]:
            with self.subTest(sales=sales, profit=profit):
                model = calculate_scenario(sales, profit)
                self.assertFalse(model.applicable)
                self.assertTrue(model.reasons)
                self.assertTrue(all(value is None for value in model.scenario.values()))

    def test_recorded_losses_are_allowed_when_cost_proxy_is_nonnegative(self):
        model = calculate_scenario(1000, -100, volume_change=0.1)
        self.assertTrue(model.applicable)
        self.assertEqual(model.scenario["profit"], -110)
        self.assertAlmostEqual(model.scenario["profit_margin"], -0.1)

    def test_missing_or_partial_measures_cannot_produce_a_scenario(self):
        missing_profit = fixture_orders().drop(columns="Profit")
        self.assertFalse(build_scenario_model(build_dashboard_model(missing_profit)).applicable)
        partial = pd.concat([fixture_orders(), fixture_orders(sales=None)], ignore_index=True)
        model = build_scenario_model(build_dashboard_model(partial))
        self.assertFalse(model.applicable)
        self.assertIn("incomplete", model.explanation)

    def test_actual_default_selection_restores_all_unrounded_values(self):
        orders, _, _ = load_verified_source(ROOT)
        dashboard = build_dashboard_model(orders)
        model = build_scenario_model(dashboard)
        self.assertEqual(model.baseline, model.scenario)
        self.assertEqual(model.baseline["sales"], dashboard.metrics["sales"])
        self.assertEqual(model.baseline["profit"], dashboard.metrics["profit"])
        self.assertEqual(model.baseline["profit_margin"], dashboard.metrics["profit_margin"])
        self.assertTrue(all(value == 0 for value in model.changes.values()))

    def test_exports_and_chart_include_the_same_values_and_model_context(self):
        dashboard = build_dashboard_model(fixture_orders(), date(2026, 1, 15), date(2026, 1, 15), ["West"], ["Technology"])
        model = build_scenario_model(dashboard, price_change=0.1)
        payloads = scenario_downloads(model)
        document = json.loads(payloads["scenario_results_json"])
        table = pd.read_csv(BytesIO(payloads["scenario_results_csv"]))
        self.assertEqual(document["baseline"]["sales"], 1000)
        self.assertEqual(document["scenario"]["profit"], 300)
        self.assertEqual(document["assumptions_as_fractional_changes"]["selling_price_change"], 0.1)
        self.assertEqual(document["filters"]["regions"], ["West"])
        self.assertEqual(document["filters"]["start_date"], "2026-01-15")
        self.assertTrue(any("not verified COGS" in item for item in document["limitations"]))
        self.assertIn("Under these assumptions", document["explanation"])
        self.assertTrue(table["model_limitations"].str.contains("not a forecast", regex=False).all())
        self.assertTrue(table["selling_price_change"].eq(0.1).all())
        figure = scenario_figure(model)
        self.assertEqual(list(figure.data[0].x), [1000, 800, 200])
        self.assertEqual(list(figure.data[1].x), [1100, 800, 300])

    def test_invalid_assumptions_are_rejected(self):
        for value in [-1.01, float("nan"), float("inf")]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                calculate_scenario(1000, 200, price_change=value)


class ScenarioWidgetTests(unittest.TestCase):
    def test_price_case_and_reset_assumptions_send_default_values_without_changing_filters(self):
        app = fixture_app(fixture_orders())
        self.assertFalse(app.exception)
        self.assertTrue(all(widget.value == 0 for widget in app.slider))
        original_filters = json.loads(app.session_state["test_downloads"]["download_context"])
        app.slider(key="scenario_price_pct").set_value(10).run()
        document = receipt(app)
        self.assertEqual(document["scenario"]["sales"], 1100)
        self.assertEqual(document["scenario"]["profit"], 300)
        displayed = next(table.value for table in app.dataframe if "Scenario" in table.value.columns)
        self.assertEqual(displayed.loc[displayed.Metric.eq("Profit"), "Scenario"].iloc[0], "300.00")
        app.slider(key="scenario_volume_pct").set_value(10)
        app.slider(key="scenario_cost_pct").set_value(10)
        app.run()
        app.button(key="reset_assumptions").click().run()
        self.assertFalse(app.exception)
        self.assertTrue(all(widget.value == 0 and widget.proto.set_value for widget in app.slider))
        self.assertEqual(receipt(app)["baseline"], receipt(app)["scenario"])
        self.assertEqual(json.loads(app.session_state["test_downloads"]["download_context"]), original_filters)

    def test_filter_changes_refresh_baseline_while_preserving_assumptions_and_exports(self):
        app = AppTest.from_string(CAPTURED_APP, default_timeout=30).run()
        app.slider(key="scenario_price_pct").set_value(10)
        app.slider(key="scenario_volume_pct").set_value(5)
        app.slider(key="scenario_cost_pct").set_value(7)
        app.run()
        app.multiselect(key="regions").set_value(["West"])
        app.multiselect(key="categories").set_value(["Furniture"])
        app.date_input(key="date_range").set_value((date(2026, 1, 1), date(2026, 6, 30)))
        app.run()
        self.assertFalse(app.exception)
        document = receipt(app)
        lines = pd.read_csv(BytesIO(app.session_state["test_downloads"]["download_order_lines"]))
        sales, profit = lines.Sales.sum(), lines.Profit.sum()
        self.assertAlmostEqual(document["baseline"]["sales"], sales)
        self.assertAlmostEqual(document["baseline"]["profit"], profit)
        self.assertAlmostEqual(document["scenario"]["sales"], sales * 1.10 * 1.05)
        self.assertAlmostEqual(document["scenario"]["implied_cost_proxy"], (sales - profit) * 1.05 * 1.07)
        self.assertEqual(document["filters"]["regions"], ["West"])
        self.assertEqual(document["filters"]["categories"], ["Furniture"])
        for key, expected in [("scenario_price_pct", 10), ("scenario_volume_pct", 5), ("scenario_cost_pct", 7)]:
            self.assertEqual(app.slider(key=key).value, expected)
        figure = next(json.loads(chart.proto.spec) for chart in app.get("plotly_chart")
                       if json.loads(chart.proto.spec).get("layout", {}).get("meta", {}).get("scope") == "scenario_planning")
        for trace in figure["data"]:
            values = document[trace["name"].lower()]
            for actual, metric in zip(trace["x"], ["sales", "implied_cost_proxy", "profit"]):
                self.assertEqual(actual, values[metric])
        app.download_button(key="download_scenario_csv").click().run()
        self.assertFalse(app.exception)
        app.button(key="reset_filters").click().run()
        self.assertEqual(receipt(app)["baseline"]["sales"], 2326534.3543000002)
        self.assertEqual(app.slider(key="scenario_price_pct").value, 10)

    def test_zero_volume_denominator_is_explicit_and_exports_null(self):
        app = fixture_app(fixture_orders())
        app.slider(key="scenario_volume_pct").set_value(-100).run()
        self.assertFalse(app.exception)
        document = receipt(app)
        self.assertEqual(document["scenario"]["sales"], 0)
        self.assertEqual(document["scenario"]["profit"], 0)
        self.assertIsNone(document["scenario"]["profit_margin"])
        self.assertEqual(document["scenario_margin_status"], "zero_scenario_sales")
        table = pd.read_csv(BytesIO(app.session_state["test_downloads"]["download_scenario_csv"]))
        self.assertTrue(table.loc[table.metric.eq("Profit Margin"), "scenario_value"].isna().all())

    def test_inapplicable_baselines_show_reason_without_scenario_results(self):
        for orders in [fixture_orders(0, 0), fixture_orders(-100, -20), fixture_orders(100, 101), fixture_orders().drop(columns="Profit")]:
            with self.subTest(columns=orders.columns.tolist(), sales=orders.Sales.tolist()):
                app = fixture_app(orders)
                self.assertFalse(app.exception)
                self.assertTrue(any("Model not applicable" in element.value for element in app.info))
                self.assertNotIn("download_scenario_json", app.session_state["test_downloads"])

    def test_empty_selection_and_recovery_preserve_assumptions(self):
        app = fixture_app(fixture_orders())
        app.slider(key="scenario_price_pct").set_value(10).run()
        app.multiselect(key="regions").set_value([]).run()
        self.assertFalse(app.exception)
        self.assertTrue(any("Model not applicable" in element.value for element in app.info))
        app.button(key="reset_filters").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(receipt(app)["scenario"]["profit"], 300)


if __name__ == "__main__":
    unittest.main()
