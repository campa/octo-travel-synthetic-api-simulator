#!/usr/bin/env python3
"""Generate a quality report for seed_data.json.

Usage:
    python scripts/quality_report.py [path/to/seed_data.json] [--save]

Options:
    --save    Save results to metrics/quality-reports/ with model name and timestamp.
"""

import json
import sys
from datetime import datetime
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from models.product import Product
from seeder.quality import QualityScorer


def _detect_model() -> str:
    """Read the current model from .env or return 'unknown'."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line.startswith("OTAS_OLLAMA_MODEL=") and not line.startswith("#"):
                return line.split("=", 1)[1].strip()
    return "unknown"


def main():
    save = "--save" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--save"]
    seed_file = args[0] if args else "seed_data.json"
    path = Path(seed_file)
    if not path.exists():
        print(f"File not found: {path}")
        sys.exit(1)

    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    model = _detect_model()
    products = [Product.model_validate(p) for p in raw]
    scorer = QualityScorer()
    batch = scorer.score_batch(products)

    # --- Summary ---
    print("=" * 60)
    print(f"  QUALITY REPORT — {len(products)} products")
    print(f"  Model: {model}")
    print("=" * 60)
    print()
    print(f"  Composite:    {batch.composite:.2f}")
    print(f"  Realism:      {batch.avg_realism:.2f}")
    print(f"  Coherence:    {batch.avg_coherence:.2f}")
    print(f"  Completeness: {batch.avg_completeness:.2f}")
    print(f"  Diversity:    {batch.diversity:.2f}")
    print()

    # --- Per-product ---
    print("-" * 60)
    print("  PER-PRODUCT SCORES")
    print("-" * 60)
    for ps in batch.product_scores:
        title = ps.title[:40].ljust(40)
        print(f"  {title}  Real={ps.realism:.2f}  Cohr={ps.coherence:.2f}  Cmpl={ps.completeness:.2f}")
    print()

    # --- Issues ---
    all_issues = batch.all_issues
    if all_issues:
        print("-" * 60)
        print(f"  ISSUES ({len(all_issues)})")
        print("-" * 60)
        for issue in all_issues:
            dim = issue.dimension.upper()[:5].ljust(5)
            print(f"  [{dim}] {issue.check}: {issue.message}")
        print()
    else:
        print("  No issues found.")
        print()

    # --- Issue summary by dimension ---
    dims: dict[str, int] = {}
    checks: dict[str, int] = {}
    for issue in all_issues:
        dims[issue.dimension] = dims.get(issue.dimension, 0) + 1
        checks[issue.check] = checks.get(issue.check, 0) + 1

    if dims:
        print("-" * 60)
        print("  ISSUE COUNTS BY DIMENSION")
        print("-" * 60)
        for dim, count in sorted(dims.items(), key=lambda x: -x[1]):
            print(f"  {dim:<20} {count}")
        print()
        print("-" * 60)
        print("  ISSUE COUNTS BY CHECK")
        print("-" * 60)
        for check, count in sorted(checks.items(), key=lambda x: -x[1]):
            print(f"  {check:<35} {count}")
        print()

    print("=" * 60)

    # --- Save to file ---
    if save:
        reports_dir = Path("metrics/quality-reports")
        reports_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        model_slug = model.replace(":", "-").replace("/", "-")
        report_path = reports_dir / f"{ts}_{model_slug}_{len(products)}p.json"
        report_data = {
            "timestamp": datetime.now().isoformat(),
            "model": model,
            "product_count": len(products),
            "seed_file": str(seed_file),
            "scores": {
                "composite": round(batch.composite, 3),
                "realism": round(batch.avg_realism, 3),
                "coherence": round(batch.avg_coherence, 3),
                "completeness": round(batch.avg_completeness, 3),
                "diversity": round(batch.diversity, 3),
            },
            "per_product": [
                {
                    "id": ps.product_id,
                    "title": ps.title,
                    "realism": round(ps.realism, 3),
                    "coherence": round(ps.coherence, 3),
                    "completeness": round(ps.completeness, 3),
                }
                for ps in batch.product_scores
            ],
            "issues": [
                {
                    "dimension": i.dimension,
                    "check": i.check,
                    "message": i.message,
                    "product_id": i.product_id,
                }
                for i in all_issues
            ],
            "issue_summary": {
                "by_dimension": dims,
                "by_check": checks,
                "total": len(all_issues),
            },
        }
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report_data, f, indent=2)
        print(f"  Saved to: {report_path}")
        print()


if __name__ == "__main__":
    main()
