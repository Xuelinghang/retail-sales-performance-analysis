"""Run Phase 1 from any working directory in an environment with project dependencies."""

import argparse
from datetime import datetime, timezone
from importlib.metadata import version
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from retail_lab.analysis import analyze_workbook, detect_excel_engine, file_hash, load_workbook, write_outputs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of-date", default="2026-11-01", help="Inspection reference date, YYYY-MM-DD; does not filter records.")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "outputs", help="Destination for generated summaries; defaults to the project outputs directory.")
    args = parser.parse_args()
    try:
        reference_date = datetime.strptime(args.as_of_date, "%Y-%m-%d").date().isoformat()
    except ValueError:
        parser.error("--as-of-date must be a valid YYYY-MM-DD calendar date.")
    source_path = PROJECT_ROOT / "data/raw/sample_-_superstore.xlsx"
    output_dir = args.output_dir.resolve()
    if output_dir.is_relative_to(source_path.parent.resolve()):
        parser.error("The output directory must not be inside data/raw.")
    source_hash_before = file_hash(source_path)
    sheets = load_workbook(source_path)
    metrics, tables = analyze_workbook(sheets, reference_date)
    source_hash_after = file_hash(source_path)
    if source_hash_before != source_hash_after:
        raise RuntimeError("The raw source changed during analysis.")
    failed = tables["reconciliation"]["status"].eq("FAIL").any() or tables["relationships"]["status"].eq("FAIL").any()
    manifest = {
        "project": "Retail Performance & Planning Lab", "phase": 1,
        "run_time_utc": datetime.now(timezone.utc).isoformat(),
        "source_path": source_path.relative_to(PROJECT_ROOT).as_posix(),
        "source_size_bytes": source_path.stat().st_size,
        "source_sha256_before": source_hash_before, "source_sha256_after": source_hash_after,
        "raw_source_unchanged": True, "excel_engine": detect_excel_engine(source_path),
        "as_of_date": reference_date, "date_filter_applied": False,
        "python_executable": Path(sys.executable).name, "python_version": sys.version.split()[0],
        "dependencies": {package: version(package) for package in ["pandas", "openpyxl", "xlrd"]},
        "metric_source": "Orders only; no enrichment joins", "row_deletion_applied": False,
        "absolute_tolerance": 1e-6, "relative_tolerance": 1e-12,
        "reconciliation_failed": bool(failed),
        "artifacts": [*(f"{name}.csv" for name in tables), "metrics.csv", "metrics.json", "run_manifest.json", "data_quality_summary.md"],
    }
    write_outputs(output_dir, metrics, tables, manifest)
    print(json.dumps(metrics, indent=2, allow_nan=False))
    print("Reconciliation:", tables["reconciliation"]["status"].value_counts().to_dict())
    print("Outputs:", output_dir)
    print("Raw source unchanged:", source_hash_after)
    if failed:
        raise SystemExit("Reconciliation failed. Inspect outputs/reconciliation.csv and relationships.csv.")


if __name__ == "__main__":
    main()
