"""Regression checks for the reported reset and chart readability defects."""

from datetime import date
from io import BytesIO
import json
import unittest

import pandas as pd
import plotly.graph_objects as go
from streamlit.testing.v1 import AppTest

from retail_lab.dashboard import build_dashboard_model, pad_horizontal_bar_labels, trend_figure
from test_dashboard import CAPTURED_APP, fixture_app, fixture_orders, overview_charts


class ResetRegressionTests(unittest.TestCase):
    def assert_selection_is_consistent(self, app):
        self.assertFalse(app.exception)
        downloads = app.session_state["test_downloads"]
        context = json.loads(downloads["download_context"])
        self.assertEqual(app.multiselect(key="regions").value, context["regions"])
        self.assertEqual(app.multiselect(key="categories").value, context["categories"])
        self.assertEqual([str(value) for value in app.date_input(key="date_range").value],
                         [context["start_date"], context["end_date_inclusive"]])
        lines = pd.read_csv(BytesIO(downloads["download_order_lines"]))
        dates = pd.to_datetime(lines["Order Date"])
        self.assertTrue(dates.between(context["start_date"], context["end_date_inclusive"]).all())
        self.assertTrue(lines["Region"].isin(context["regions"]).all())
        self.assertTrue(lines["Category"].isin(context["categories"]).all())
        self.assertEqual(len(lines), context["order_line_count"])
        cards = {element.label: element.value for element in app.metric}
        for name in ["Sales", "Profit"]:
            self.assertEqual(cards[name], f"{lines[name].sum():,.2f}")
        for chart in overview_charts(app):
            spec = json.loads(chart.proto.spec)
            trace = spec["data"][0]
            values = trace["y"] if spec["layout"]["title"]["text"].startswith("Monthly") else trace["x"]
            measure = "Sales" if "sales" in spec["layout"]["title"]["text"].lower() else "Profit"
            self.assertAlmostEqual(sum(value for value in values if value is not None), lines[measure].sum())
        driver_context = json.loads(downloads["download_drivers_context"])
        self.assertEqual(driver_context["regions"], context["regions"])
        self.assertEqual(driver_context["categories"], context["categories"])

    def test_reset_sends_browser_values_and_subsequent_single_filter_changes_stay_consistent(self):
        app = AppTest.from_string(CAPTURED_APP, default_timeout=30).run()
        full_dates = app.date_input(key="date_range").value
        full_regions = app.multiselect(key="regions").value.copy()
        full_categories = app.multiselect(key="categories").value.copy()
        app.date_input(key="date_range").set_value((date(2026, 2, 10), date(2026, 5, 20)))
        app.multiselect(key="regions").set_value(["West"])
        app.multiselect(key="categories").set_value(["Furniture"])
        app.run()
        app.button(key="reset_filters").click().run()
        self.assert_selection_is_consistent(app)
        self.assertEqual(app.date_input(key="date_range").value, full_dates)
        self.assertEqual(app.multiselect(key="regions").value, full_regions)
        self.assertEqual(app.multiselect(key="categories").value, full_categories)
        self.assertEqual(app.metric[0].value, "2,326,534.35")
        self.assertEqual(app.metric[1].value, "292,296.81")
        # This is the browser synchronization instruction that key deletion
        # failed to emit, even when the backend itself showed full totals.
        for widget in [app.date_input(key="date_range"), app.multiselect(key="regions"), app.multiselect(key="categories")]:
            self.assertTrue(widget.proto.set_value)
        self.assertEqual(app.selectbox(key="drivers_baseline_month").value, "2026-10")
        self.assertEqual(app.selectbox(key="drivers_comparison_month").value, "2026-11")
        app.multiselect(key="regions").set_value(["East"]).run()
        self.assert_selection_is_consistent(app)
        self.assertEqual(app.multiselect(key="categories").value, full_categories)
        self.assertEqual(app.date_input(key="date_range").value, full_dates)
        app.button(key="reset_filters").click().run()
        app.multiselect(key="categories").set_value(["Technology"]).run()
        self.assert_selection_is_consistent(app)
        self.assertEqual(app.multiselect(key="regions").value, full_regions)
        app.button(key="reset_filters").click().run()
        app.date_input(key="date_range").set_value((date(2025, 1, 1), date(2025, 12, 31))).run()
        self.assert_selection_is_consistent(app)
        self.assertEqual(app.multiselect(key="regions").value, full_regions)
        self.assertEqual(app.multiselect(key="categories").value, full_categories)

    def test_reset_restores_undated_checkbox_when_present(self):
        orders = fixture_orders()
        orders.loc[0, "Order Date"] = "invalid"
        app = fixture_app(orders)
        app.checkbox(key="include_undated").uncheck().run()
        self.assertEqual(app.metric[0].value, "500.00")
        app.button(key="reset_filters").click().run()
        self.assertFalse(app.exception)
        self.assertTrue(app.checkbox(key="include_undated").value)
        self.assertTrue(app.checkbox(key="include_undated").proto.set_value)
        self.assertEqual(app.metric[0].value, "600.00")


class ChartReadabilityTests(unittest.TestCase):
    def test_horizontal_label_padding_preserves_zero_and_values_for_signed_ranges(self):
        for values in [[16580, 9000], [-16580, -9000], [-9000, 16580], [0, 0]]:
            with self.subTest(values=values):
                figure = go.Figure(go.Bar(x=values, y=["A", "B"], orientation="h", text=values))
                pad_horizontal_bar_labels(figure, values)
                low, high = figure.layout.xaxis.range
                self.assertLessEqual(low, 0)
                self.assertGreaterEqual(high, 0)
                self.assertLess(low, high)
                if min(values) < 0:
                    self.assertLess(low, min(values))
                if max(values) > 0:
                    self.assertGreater(high, max(values))
                self.assertEqual(list(figure.data[0].x), values)
                self.assertEqual(list(figure.data[0].text), [str(value) for value in values])
                self.assertFalse(figure.data[0].cliponaxis)

    def test_monthly_tick_density_does_not_change_data_or_remove_calendar_gaps(self):
        orders = fixture_orders().iloc[[0, 3]].copy()
        orders.loc[3, "Order Date"] = "2026-03-15"
        figure = trend_figure(build_dashboard_model(orders), "sales")
        self.assertEqual(list(figure.data[0].y), [100, None, 150])
        self.assertEqual(figure.data[0].x[0], "2026-01-01")
        self.assertEqual(figure.data[0].x[-1], "2026-03-01")
        self.assertEqual(figure.layout.xaxis.tickmode, "auto")
        self.assertLessEqual(figure.layout.xaxis.nticks, 4)
        self.assertIsNone(figure.layout.xaxis.tickvals)


if __name__ == "__main__":
    unittest.main()
