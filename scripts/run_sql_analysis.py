"""Execute detail-level SQL and reconcile every result with independent pandas calculations."""

import argparse
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from retail_lab.analysis import file_hash, load_workbook


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", type=date.fromisoformat)
    parser.add_argument("--end-date", type=date.fromisoformat)
    parser.add_argument("--regions", nargs="*", default=None)
    parser.add_argument("--categories", nargs="*", default=None)
    parser.add_argument("--exclude-undated", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "outputs/sql")
    args = parser.parse_args()
    if (args.start_date is None) != (args.end_date is None):
        parser.error("Provide both --start-date and --end-date, or neither.")
    if args.start_date and args.start_date > args.end_date:
        parser.error("--start-date must not be after --end-date.")
    # Keep DuckDB optional for the deployed app and the runtime-only test suite.
    try:
        from retail_lab.sql_analysis import QUERY_FILES, UnsafeJoinError, run_sql_analysis
    except ModuleNotFoundError as error:
        parser.error(f"Missing SQL dependency ({error.name}). Run python -m pip install -r requirements-sql.txt.")
    source = PROJECT_ROOT / "data/raw/sample_-_superstore.xlsx"
    output = args.output_dir.resolve()
    if output.is_relative_to((PROJECT_ROOT / "data").resolve()):
        parser.error("SQL outputs must not be written inside data/.")
    output.mkdir(parents=True, exist_ok=True)
    before = file_hash(source)
    manifest = {
        "run_time_utc": datetime.now(timezone.utc).isoformat(),
        "source_path": source.relative_to(PROJECT_ROOT).as_posix(),
        "source_sha256_before": before,
        "queries": {name: file_hash(PROJECT_ROOT / "sql" / name)
                    for name in ["00_selected_orders.sql", *QUERY_FILES.values()]},
        "status": "FAILED",
    }
    exit_code = 1
    try:
        report = run_sql_analysis(
            load_workbook(source), start=args.start_date.isoformat() if args.start_date else None,
            end=args.end_date.isoformat() if args.end_date else None,
            regions=args.regions, categories=args.categories, include_undated=not args.exclude_undated,
        )
        for name, table in report.results.items():
            table.to_csv(output / f"{name}.csv", index=False)
        report.mapping.to_csv(output / "column_mapping.csv", index=False)
        report.reconciliation.to_csv(output / "reconciliation.csv", index=False)
        failed = report.reconciliation["status"].eq("FAIL").any()
        manifest.update(report.context)
        manifest["status"] = "FAILED" if failed else "PASS"
        manifest["reconciliation_counts"] = report.reconciliation["status"].value_counts().to_dict()
        print(report.results["kpi_summary"].to_string(index=False))
        print("Reconciliation:", manifest["reconciliation_counts"])
        exit_code = 1 if failed else 0
    except UnsafeJoinError as error:
        error.checks.to_csv(output / "join_key_checks.csv", index=False)
        manifest["error"] = str(error)
        manifest["prior_outputs_are_not_current"] = True
        print(str(error), file=sys.stderr)
    except Exception as error:
        manifest["error"] = str(error)
        manifest["prior_outputs_are_not_current"] = True
        print(f"SQL analysis failed: {error}", file=sys.stderr)
    finally:
        after = file_hash(source)
        manifest["source_sha256_after"] = after
        manifest["raw_source_unchanged"] = before == after
        if before != after:
            manifest["status"] = "FAILED"
            manifest["error"] = "Raw source changed during SQL analysis."
            exit_code = 1
        (output / "run_manifest.json").write_text(json.dumps(manifest, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print("Outputs:", output)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
