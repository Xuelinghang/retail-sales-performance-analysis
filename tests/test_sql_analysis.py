"""Optional DuckDB computations, unsafe joins, calendar gaps, and CLI validation."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pandas as pd

SQL_AVAILABLE = importlib.util.find_spec("duckdb") is not None
if SQL_AVAILABLE:
    from retail_lab.sql_analysis import UnsafeJoinError, run_sql_analysis
    from retail_lab.analysis import file_hash, load_workbook

ROOT = Path(__file__).resolve().parents[1]


def fixture():
    return {
        "Orders": pd.DataFrame({
            "Row ID": [1, 2, 3, 4], "Order ID": ["A", "A", "B", "C"],
            "Order Date": ["2026-01-01", "2026-01-31", "2026-03-01", "invalid"],
            "Region": ["West", "West", "East", "East"],
            "Category": ["Furniture", "Technology", "Furniture", "Technology"],
            "Sub-Category": ["Chairs", "Phones", "Chairs", None],
            "Sales": [100.0, 300.0, 50.0, 150.0], "Profit": [-10.0, 60.0, -5.0, 30.0],
        }),
        "People": pd.DataFrame({"Region": ["West", "East"], "Regional Manager": ["W", "E"]}),
        "Returns": pd.DataFrame({"Order ID": ["A"], "Returned": ["Yes"]}),
    }


@unittest.skipUnless(SQL_AVAILABLE, "Install requirements-sql.txt to run optional SQL checks")
class SqlAnalysisTests(unittest.TestCase):
    def run_checked(self, sheets=None, **filters):
        report = run_sql_analysis(sheets if sheets is not None else fixture(), **filters)
        failed = report.reconciliation.loc[report.reconciliation["status"].eq("FAIL")]
        self.assertTrue(failed.empty, failed.to_string(index=False))
        return report

    def test_detail_kpis_and_order_level_enrichment_preserve_all_lines(self):
        report = self.run_checked()
        kpi = report.results["kpi_summary"].iloc[0]
        self.assertEqual(kpi["sales"], 600)
        self.assertEqual(kpi["profit"], 75)
        self.assertEqual(kpi["profit_margin"], 0.125)
        self.assertEqual(kpi["order_count"], 3)
        self.assertEqual(kpi["order_line_count"], 4)
        enriched = report.results["order_enrichment"]
        self.assertEqual(enriched["order_return_flag"].notna().sum(), 2)
        self.assertEqual(enriched["assigned_regional_manager"].tolist(), ["W", "W", "E", "E"])

    def test_calendar_gap_is_not_zero_or_a_skipped_lag(self):
        report = self.run_checked()
        monthly = report.results["monthly_trends"]
        january, february, march = monthly.iloc[:3].to_dict("records")
        self.assertEqual(january["sales"], 400)
        self.assertEqual(february["record_status"], "no_records")
        self.assertTrue(pd.isna(february["sales"]))
        self.assertEqual(march["previous_month"].month, 2)
        self.assertTrue(pd.isna(march["sales_mom_change"]))
        self.assertEqual(march["sales_mom_status"], "previous_no_records")

    def test_invalid_dates_are_separate_and_remain_in_totals(self):
        report = self.run_checked()
        row = report.results["monthly_trends"].query("record_type == 'undated'").iloc[0]
        self.assertEqual(row["sales"], 150)
        self.assertEqual(row["order_line_count"], 1)
        self.assertEqual(row["sales_mom_status"], "not_calendar_month")
        sheets = fixture()
        sheets["Orders"]["Order Date"] = [None, "bad", 45200, None]
        all_undated = self.run_checked(sheets).results["monthly_trends"]
        self.assertEqual(len(all_undated), 1)
        self.assertEqual(all_undated.iloc[0]["order_line_count"], 4)

    def test_zero_denominator_and_blank_order_identifiers(self):
        sheets = fixture()
        sheets["Orders"]["Sales"] = [100, -100, 0, 0]
        sheets["Orders"]["Order ID"] = ["A", "A", "   ", None]
        kpi = self.run_checked(sheets).results["kpi_summary"].iloc[0]
        self.assertTrue(pd.isna(kpi["profit_margin"]))
        self.assertEqual(kpi["profit_margin_status"], "zero_sales")
        self.assertEqual(kpi["order_count"], 1)
        self.assertEqual(kpi["order_id_missing_line_count"], 2)

    def test_missing_and_nonfinite_amounts_do_not_become_zero(self):
        sheets = fixture()
        sheets["Orders"]["Sales"] = [100, None, float("inf"), "bad"]
        sheets["Orders"]["Profit"] = [None, 60, -5, 30]
        kpi = self.run_checked(sheets).results["kpi_summary"].iloc[0]
        self.assertEqual(kpi["sales"], 100)
        self.assertEqual(kpi["sales_status"], "partial")
        self.assertEqual(kpi["sales_unavailable_line_count"], 3)
        self.assertEqual(kpi["profit_status"], "partial")
        self.assertTrue(pd.isna(kpi["profit_margin"]))
        sheets["Orders"]["Sales"] = [None] * 4
        self.assertTrue(pd.isna(self.run_checked(sheets).results["kpi_summary"].iloc[0]["sales"]))

    def test_missing_profit_field_is_unavailable(self):
        sheets = fixture()
        sheets["Orders"] = sheets["Orders"].drop(columns="Profit")
        report = self.run_checked(sheets)
        kpi = report.results["kpi_summary"].iloc[0]
        self.assertEqual(kpi["profit_status"], "field_missing")
        self.assertEqual(kpi["profit_margin_status"], "profit_field_missing")
        self.assertTrue(report.results["product_profitability"]["profit_rank"].isna().all())

    def test_dense_rank_ties_losses_and_missing_classification(self):
        sheets = fixture()
        sheets["Orders"]["Sub-Category"] = ["A", "B", "C", "   "]
        sheets["Orders"]["Profit"] = [20, 20, -5, None]
        product = self.run_checked(sheets).results["product_profitability"]
        self.assertEqual(product.iloc[:3]["profit_rank"].tolist(), [1, 1, 2])
        self.assertTrue(product.iloc[2]["is_loss"])
        self.assertTrue(product.iloc[3]["is_missing_group"])
        self.assertTrue(pd.isna(product.iloc[3]["profit_rank"]))

    def test_duplicate_lookup_keys_abort_before_join_for_both_tables(self):
        for sheet in ["People", "Returns"]:
            with self.subTest(sheet=sheet):
                sheets = fixture()
                sheets[sheet] = pd.concat([sheets[sheet], sheets[sheet].iloc[:1]], ignore_index=True)
                with self.assertRaises(UnsafeJoinError) as raised:
                    run_sql_analysis(sheets, regions=["East"])
                self.assertEqual(raised.exception.checks["duplicate_key_rows"].sum(), 1)

    def test_null_or_blank_lookup_keys_abort(self):
        for sheet, field in [("People", "Region"), ("Returns", "Order ID")]:
            for value in [None, " \t "]:
                with self.subTest(sheet=sheet, value=value):
                    sheets = fixture()
                    sheets[sheet].loc[0, field] = value
                    with self.assertRaises(UnsafeJoinError):
                        run_sql_analysis(sheets)

    def test_zero_and_negative_prior_values_keep_absolute_change_only(self):
        sheets = fixture()
        sheets["Orders"]["Order Date"] = ["2026-01-01", "2026-02-01", "2026-03-01", "2026-04-01"]
        sheets["Orders"]["Sales"] = [0, 100, -50, 200]
        sheets["Orders"]["Profit"] = [-10, 20, 0, 30]
        monthly = self.run_checked(sheets).results["monthly_trends"]
        self.assertEqual(monthly.iloc[1]["sales_mom_status"], "previous_zero")
        self.assertEqual(monthly.iloc[1]["sales_mom_change"], 100)
        self.assertEqual(monthly.iloc[1]["profit_mom_status"], "previous_negative")
        self.assertEqual(monthly.iloc[1]["profit_mom_change"], 30)
        self.assertEqual(monthly.iloc[3]["sales_mom_status"], "previous_negative")
        self.assertEqual(monthly.iloc[3]["sales_mom_change"], 250)
        self.assertTrue(pd.isna(monthly.iloc[3]["sales_mom_rate"]))

    def test_parameter_filters_undated_policy_and_injection_string(self):
        report = self.run_checked(start="2026-01-15", end="2026-03-01", regions=["West"], categories=["Technology"])
        self.assertEqual(report.results["kpi_summary"].iloc[0]["sales"], 300)
        self.assertEqual(self.run_checked(regions=["West' OR TRUE --"]).results["kpi_summary"].iloc[0]["order_line_count"], 0)
        report = self.run_checked(start="2026-01-01", end="2026-01-31", include_undated=False)
        self.assertEqual(report.results["kpi_summary"].iloc[0]["sales"], 400)

    def test_empty_selection_and_absent_optional_dimensions(self):
        report = self.run_checked(regions=[])
        self.assertEqual(report.results["kpi_summary"].iloc[0]["order_line_count"], 0)
        self.assertTrue(pd.isna(report.results["kpi_summary"].iloc[0]["sales"]))
        sheets = fixture()
        sheets["Orders"] = sheets["Orders"].drop(columns=["Region", "Category", "Sub-Category", "Order Date"])
        self.assertEqual(len(self.run_checked(sheets).results["product_profitability"]), 1)

    def test_sql_reads_details_not_python_metrics_and_reconciliation_detects_tampering(self):
        with TemporaryDirectory() as directory:
            copied = Path(directory) / "sql"
            copied.mkdir()
            for path in (ROOT / "sql").glob("*.sql"):
                query = path.read_text(encoding="utf-8")
                if path.name == "01_kpi_summary.sql":
                    query = query.replace("SUM(sales) AS sales", "SUM(sales) + 1 AS sales")
                (copied / path.name).write_text(query, encoding="utf-8")
            report = run_sql_analysis(fixture(), project_root=directory)
            self.assertTrue(report.reconciliation["status"].eq("FAIL").any())

    def test_cli_nonzero_for_failed_reconciliation_and_unsafe_join(self):
        from scripts import run_sql_analysis as cli
        from retail_lab import sql_analysis
        original = sql_analysis.run_sql_analysis

        def mismatched(*args, **kwargs):
            report = original(*args, **kwargs)
            report.reconciliation.loc[0, "status"] = "FAIL"
            return report

        for failure in ["reconciliation", "keys"]:
            with self.subTest(failure=failure), TemporaryDirectory() as directory:
                sheets = fixture()
                if failure == "keys":
                    sheets["Returns"] = pd.concat([sheets["Returns"]] * 2, ignore_index=True)
                with patch.object(sys, "argv", ["run_sql_analysis.py", "--output-dir", directory]), \
                     patch.object(cli, "load_workbook", return_value=sheets), \
                     patch.object(sql_analysis, "run_sql_analysis", side_effect=mismatched if failure == "reconciliation" else original):
                    self.assertNotEqual(cli.main(), 0)
                manifest = json.loads((Path(directory) / "run_manifest.json").read_text())
                self.assertEqual(manifest["status"], "FAILED")
                if failure == "keys":
                    self.assertFalse((Path(directory) / "order_enrichment.csv").exists())
                    self.assertTrue((Path(directory) / "join_key_checks.csv").exists())

    def test_full_workbook_and_cli_from_another_working_directory(self):
        source = ROOT / "data/raw/sample_-_superstore.xlsx"
        before = file_hash(source)
        with TemporaryDirectory() as directory:
            completed = subprocess.run([sys.executable, str(ROOT / "scripts/run_sql_analysis.py"),
                                        "--output-dir", str(Path(directory) / "sql")],
                                       cwd=directory, capture_output=True, text=True, timeout=60)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            result = pd.read_csv(Path(directory) / "sql/kpi_summary.csv").iloc[0]
            baseline = json.loads((ROOT / "outputs/metrics.json").read_text())
            self.assertAlmostEqual(result["sales"], baseline["sales"], places=6)
            self.assertEqual(result["order_count"], 5111)
        self.assertEqual(file_hash(source), before)


if __name__ == "__main__":
    unittest.main()
