"""Exercise real entry points without relying on the repository working directory."""

import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

import pandas as pd
from streamlit.testing.v1 import AppTest

from retail_lab.analysis import file_hash

ROOT = Path(__file__).resolve().parents[1]


class DeploymentTests(unittest.TestCase):
    def test_analysis_from_another_directory_preserves_baseline_and_source(self):
        source = ROOT / "data/raw/sample_-_superstore.xlsx"
        before = file_hash(source)
        with TemporaryDirectory() as directory:
            output = Path(directory) / "audit"
            completed = subprocess.run(
                [sys.executable, str(ROOT / "scripts/run_analysis.py"),
                 "--output-dir", str(output)], cwd=directory,
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            baseline = json.loads((ROOT / "outputs/metrics.json").read_text(encoding="utf-8"))
            observed = json.loads((output / "metrics.json").read_text(encoding="utf-8"))
            self.assertEqual(observed, baseline)
            self.assertFalse(pd.read_csv(output / "reconciliation.csv")["status"].eq("FAIL").any())
            manifest = json.loads((output / "run_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["source_path"], "data/raw/sample_-_superstore.xlsx")
            self.assertEqual(manifest["python_executable"], Path(sys.executable).name)
        self.assertEqual(file_hash(source), before)

    def test_real_app_entry_from_another_directory(self):
        previous = Path.cwd()
        with TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                app = AppTest.from_file(str(ROOT / "dashboard/app.py"), default_timeout=60).run()
                self.assertFalse(app.exception, str(app.exception))
                self.assertFalse(app.error, str(app.error))
                self.assertEqual(app.title[0].value, "Retail Performance & Planning Lab")
                self.assertEqual(app.metric[0].value, "2,326,534.35")
                self.assertIn("Scenario Planning", [tab.label for tab in app.tabs])
            finally:
                os.chdir(previous)


if __name__ == "__main__":
    unittest.main()
