"""Export a reproducible monthly driver comparison without starting Streamlit."""

import argparse
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from retail_lab.dashboard import load_verified_source
from retail_lab.drivers import build_driver_model, driver_downloads, month_plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-month")
    parser.add_argument("--comparison-month")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--regions", nargs="*", default=None)
    parser.add_argument("--categories", nargs="*", default=None)
    args = parser.parse_args()
    orders, quality, source_hash = load_verified_source(PROJECT_ROOT)
    plan = month_plan(orders, args.start_date, args.end_date)
    if plan["defaults"] is None:
        parser.error("At least two observed months are needed within the date window.")
    baseline = args.baseline_month or plan["defaults"][0]
    comparison = args.comparison_month or plan["defaults"][1]
    model = build_driver_model(orders, baseline, comparison, args.start_date, args.end_date, args.regions, args.categories)
    model.context["source_sha256"] = source_hash
    output_dir = PROJECT_ROOT / "outputs/drivers"
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, payload in driver_downloads(model).items():
        extension = "json" if name == "drivers_context" else "csv"
        (output_dir / f"{name}.{extension}").write_bytes(payload)
    (output_dir / "business_summary.md").write_text(
        "# Performance Drivers\n\n" + f"{baseline} to {comparison}. Data completeness is unverified.\n\n"
        + "\n".join("- " + sentence for sentence in model.summary) + "\n", encoding="utf-8")
    print(model.comparison.to_string(index=False))
    print("Reconciliation:", model.reconciliation["status"].value_counts().to_dict())
    print("Outputs:", output_dir)


if __name__ == "__main__":
    main()
