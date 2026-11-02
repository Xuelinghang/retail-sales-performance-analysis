"""Selection, figure, export, and Streamlit widget behavior checks."""

from datetime import date
from io import BytesIO
import json
import math
from pathlib import Path
import unittest

import pandas as pd
from streamlit.testing.v1 import AppTest

from retail_lab.analysis import file_hash
from retail_lab.dashboard import (
    build_dashboard_model, build_figures, date_bounds, dimension_options,
    download_payloads, load_verified_source, partial_months,
)

ROOT = Path(__file__).resolve().parents[1]


def fixture_orders():
    return pd.DataFrame({
        "Row ID": [1, 2, 3, 4], "Order ID": ["A", "A", "B", "C"],
        "Order Date": ["2026-01-01", "2026-01-15", "2026-02-01", "2026-02-28"],
        "Region": ["West", "West", "East", "West"],
        "Category": ["Furniture", "Technology", "Furniture", "Furniture"],
        "Sub-Category": ["Tables", "Phones", "Tables", "Chairs"],
        "Sales": [100, 300, 50, 150], "Profit": [-10, 60, -5, 30],
    })


def fixture_app(orders):
    source = "from dashboard.app import render_dashboard\nimport pandas as pd\n"
    source += f"render_dashboard(pd.DataFrame({orders.to_dict('list')!r}))\n"
    return AppTest.from_string(source, default_timeout=30).run()


def overview_charts(app):
    # Drivers and scenario charts have distinct scopes from overview totals.
    return [chart for chart in app.get("plotly_chart")
            if json.loads(chart.proto.spec).get("layout", {}).get("meta", {}).get("scope")
            not in {"performance_drivers", "scenario_planning"}]


CAPTURED_APP = '''
from dashboard.app import main
from unittest.mock import patch
import streamlit as st

captured = {}
original_download = st.download_button
def capture_download(label, data, **kwargs):
    captured[kwargs['key']] = data
    return original_download(label, data, **kwargs)
with patch.object(st, 'download_button', side_effect=capture_download):
    main()
st.session_state['test_downloads'] = captured
'''


class DashboardModelTests(unittest.TestCase):
    def test_combined_filters_are_inclusive_and_do_not_mutate_source(self):
        orders = fixture_orders()
        original = orders.copy(deep=True)
        model = build_dashboard_model(orders, date(2026, 1, 10), date(2026, 2, 28), ["West"], ["Furniture"])
        self.assertEqual(model.details["Row ID"].tolist(), [4])
        self.assertEqual(model.metrics["sales"], 150)
        self.assertEqual(model.metrics["profit"], 30)
        self.assertEqual(model.metrics["order_count"], 1)
        pd.testing.assert_frame_equal(orders, original)

    def test_empty_multiselect_is_empty_not_all(self):
        model = build_dashboard_model(fixture_orders(), regions=[])
        self.assertTrue(model.details.empty)
        self.assertEqual(model.metrics["order_count"], 0)
        self.assertTrue(all(value is None for value in build_figures(model).values()))
        exported = pd.read_csv(BytesIO(download_payloads(model)["order_lines"]))
        self.assertTrue(exported.empty)

    def test_export_and_chart_values_match_known_totals(self):
        model = build_dashboard_model(fixture_orders())
        exports = download_payloads(model)
        self.assertEqual(len(pd.read_csv(BytesIO(exports["order_lines"]))), 4)
        for name in ["monthly", "regional", "category"]:
            frame = pd.read_csv(BytesIO(exports[name]))
            self.assertEqual(frame["sales"].sum(), 600)
            self.assertEqual(frame["profit"].sum(), 75)
            self.assertEqual(frame["order_line_count"].sum(), 4)
        for name, figure in build_figures(model).items():
            trace = figure.to_plotly_json()["data"][0]
            values = trace["y"] if name.startswith("monthly") else trace["x"]
            expected = 600 if name.endswith("sales") else 75
            self.assertEqual(sum(value for value in values if value is not None), expected)
        self.assertIn("Tables (loss)", build_figures(model)["subcategory_profit"].data[0].y)

    def test_missing_profit_is_unavailable_in_cards_charts_and_csv(self):
        model = build_dashboard_model(fixture_orders().drop(columns="Profit"))
        self.assertIsNone(model.metrics["profit"])
        self.assertIsNone(model.metrics["profit_margin"])
        self.assertIsNone(build_figures(model)["monthly_profit"])
        self.assertIsNone(build_figures(model)["regional_profit"])
        self.assertIsNone(build_figures(model)["subcategory_profit"])
        self.assertTrue(pd.read_csv(BytesIO(download_payloads(model)["monthly"]))["profit"].isna().all())

    def test_invalid_dates_are_retained_unless_explicitly_excluded(self):
        orders = fixture_orders()
        orders.loc[0, "Order Date"] = "invalid"
        model = build_dashboard_model(orders, date(2026, 1, 1), date(2026, 2, 28))
        self.assertEqual(model.metrics["sales"], 600)
        self.assertEqual(model.context["undated_order_line_count"], 1)
        monthly = pd.read_csv(BytesIO(download_payloads(model)["monthly"]))
        self.assertEqual(monthly.loc[monthly["Month"].eq("__UNASSIGNED__"), "sales"].iloc[0], 100)
        dated = build_dashboard_model(orders, date(2026, 1, 1), date(2026, 2, 28), include_undated=False)
        self.assertEqual(dated.metrics["sales"], 500)
        orders["Order Date"] = "invalid"
        self.assertTrue(build_dashboard_model(orders, include_undated=False).details.empty)

    def test_time_series_has_gaps_without_invented_zeros(self):
        orders = fixture_orders().iloc[[0, 3]].copy()
        orders.loc[3, "Order Date"] = "2026-03-15"
        trace = build_figures(build_dashboard_model(orders))["monthly_sales"].data[0]
        self.assertEqual(list(trace.y), [100, None, 150])
        self.assertFalse(trace.connectgaps)

    def test_partial_month_detection(self):
        self.assertEqual(partial_months(date(2026, 1, 1), date(2026, 2, 28)), [])
        self.assertEqual(partial_months(date(2026, 1, 10), date(2026, 2, 20)), ["Jan 2026", "Feb 2026"])

    def test_default_metrics_match_phase1_with_source_integrity(self):
        before = file_hash(ROOT / "data/raw/sample_-_superstore.xlsx")
        orders, quality, source_hash = load_verified_source(ROOT)
        bounds = date_bounds(orders)
        model = build_dashboard_model(orders, *bounds, dimension_options(orders, "Region"), dimension_options(orders, "Category"))
        baseline = json.loads((ROOT / "outputs/metrics.json").read_text(encoding="utf-8"))
        for metric in ["sales", "profit", "profit_margin", "order_count", "order_line_count"]:
            self.assertTrue(math.isclose(model.metrics[metric], baseline[metric], abs_tol=1e-6, rel_tol=1e-12))
        self.assertFalse(quality["reconciliation"]["status"].eq("FAIL").any())
        self.assertEqual(before, source_hash)
        self.assertEqual(file_hash(ROOT / "data/raw/sample_-_superstore.xlsx"), before)


class DashboardWidgetTests(unittest.TestCase):
    def test_combined_widgets_drive_cards_figures_table_and_actual_download_bytes(self):
        app = AppTest.from_string(CAPTURED_APP, default_timeout=30).run()
        self.assertFalse(app.exception)
        app.date_input(key="date_range").set_value((date(2026, 2, 10), date(2026, 5, 20)))
        app.multiselect(key="regions").set_value(["West"])
        app.multiselect(key="categories").set_value(["Furniture"])
        app.run()
        self.assertFalse(app.exception)
        downloads = app.session_state["test_downloads"]
        lines = pd.read_csv(BytesIO(downloads["download_order_lines"]))
        dates = pd.to_datetime(lines["Order Date"])
        self.assertTrue(dates.between("2026-02-10", "2026-05-20").all())
        self.assertTrue(lines["Region"].eq("West").all())
        self.assertTrue(lines["Category"].eq("Furniture").all())
        self.assertGreater(len(lines), 0)
        sales, profit = lines["Sales"].sum(), lines["Profit"].sum()
        cards = {metric.label: metric.value for metric in app.metric}
        self.assertEqual(cards["Sales"], f"{sales:,.2f}")
        self.assertEqual(cards["Profit"], f"{profit:,.2f}")
        self.assertEqual(cards["Profit Margin"], f"{profit / sales:.2%}")
        self.assertEqual(cards["Order Count"], f"{lines['Order ID'].nunique():,}")
        for name in ["monthly", "regional", "category"]:
            summary = pd.read_csv(BytesIO(downloads[f"download_{name}"]))
            self.assertAlmostEqual(summary["sales"].sum(), sales)
            self.assertAlmostEqual(summary["profit"].sum(), profit)
            self.assertEqual(summary["order_line_count"].sum(), len(lines))
        for chart in overview_charts(app):
            spec = json.loads(chart.proto.spec)
            trace = spec["data"][0]
            values = trace["y"] if spec["layout"]["title"]["text"].startswith("Monthly") else trace["x"]
            expected = sales if "sales" in spec["layout"]["title"]["text"].lower() else profit
            self.assertAlmostEqual(sum(value for value in values if value is not None), expected)
        displayed = next(table.value for table in app.dataframe if "Row ID" in table.value)
        self.assertEqual(displayed["Row ID"].tolist(), lines["Row ID"].tolist())
        context = json.loads(downloads["download_context"])
        self.assertEqual(context["order_line_count"], len(lines))
        self.assertTrue(any("Feb 2026" in warning.value for warning in app.warning))
        app.download_button(key="download_monthly").click().run()
        self.assertFalse(app.exception)
        app.button(key="reset_filters").click().run()
        self.assertEqual(app.metric[0].value, "2,326,534.35")

    def test_cleared_region_and_category_selections_show_empty_state(self):
        app = fixture_app(fixture_orders())
        app.multiselect(key="regions").set_value([]).run()
        self.assertFalse(app.exception)
        self.assertTrue(any("No records match" in element.value for element in app.info))
        self.assertEqual(len(overview_charts(app)), 0)
        self.assertEqual(app.metric[3].value, "0")
        app.button(key="reset_filters").click().run()
        app.multiselect(key="categories").set_value([]).run()
        self.assertFalse(app.exception)
        self.assertEqual(app.metric[3].value, "0")

    def test_zero_sales_has_explicit_undefined_margin(self):
        orders = fixture_orders()
        orders["Sales"] = 0
        app = fixture_app(orders)
        self.assertFalse(app.exception)
        self.assertEqual(app.metric[0].value, "0.00")
        self.assertEqual(app.metric[2].value, "Unavailable")
        self.assertTrue(any("Sales is zero" in element.value for element in app.info))

    def test_missing_profit_has_warnings_and_no_fabricated_profit_charts(self):
        app = fixture_app(fixture_orders().drop(columns="Profit"))
        self.assertFalse(app.exception)
        self.assertEqual(app.metric[1].value, "Unavailable")
        self.assertEqual(app.metric[2].value, "Unavailable")
        self.assertEqual(len(overview_charts(app)), 2)
        self.assertTrue(any("Profit field is missing" in warning.value for warning in app.warning))

    def test_missing_required_fields_stop_cleanly_with_an_actionable_message(self):
        for field in ["Sales", "Order ID"]:
            with self.subTest(field=field):
                app = fixture_app(fixture_orders().drop(columns=field))
                self.assertFalse(app.exception)
                self.assertTrue(any(field in error.value for error in app.error))
                self.assertEqual(len(app.metric), 0)

    def test_missing_optional_fields_disable_related_views(self):
        app = fixture_app(fixture_orders().drop(columns=["Order Date", "Region", "Category", "Sub-Category"]))
        self.assertFalse(app.exception)
        self.assertEqual(app.metric[0].value, "600.00")
        self.assertEqual(len(overview_charts(app)), 0)
        self.assertEqual(len(app.warning), 4)

    def test_incomplete_date_range_has_prompt_without_exception(self):
        app = fixture_app(fixture_orders())
        app.date_input(key="date_range").set_value((date(2026, 1, 15),)).run()
        self.assertFalse(app.exception)
        self.assertTrue(any("Select both" in element.value for element in app.info))


if __name__ == "__main__":
    unittest.main()
