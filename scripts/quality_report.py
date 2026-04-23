#!/usr/bin/env python3
"""Generate a quality report for seed product and availability data.

Output is organized into three sections:
  1. PRODUCT QUALITY — scores and issues for product/option/unit data
  2. AVAILABILITY QUALITY — scores and issues for availability calendar data
  3. COMBINED SUMMARY — overall scores across both datasets

Usage:
    python scripts/quality_report.py [path/to/seed_product_data.json] [--save]

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
from seeder.availability_quality import AvailabilityQualityScorer


def _detect_model() -> str:
    """Read the current model from .env or return 'unknown'."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line.startswith("OTAS_OLLAMA_MODEL=") and not line.startswith("#"):
                return line.split("=", 1)[1].strip()
    return "unknown"


def _detect_seed_product_file() -> str:
    """Read the seed product file path from .env or return the default."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line.startswith("OTAS_SEED_PRODUCT_FILE=") and not line.startswith("#"):
                return line.split("=", 1)[1].strip()
    return "data/seed_product_data.json"


def _detect_seed_availability_file() -> str:
    """Read the seed availability file path from .env or return the default."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line.startswith("OTAS_SEED_AVAILABILITY_FILE=") and not line.startswith("#"):
                return line.split("=", 1)[1].strip()
    return "data/seed_availability_data.json"


def _issue_summary(issues):
    """Build dimension and check count dicts from a list of issues."""
    dims: dict[str, int] = {}
    checks: dict[str, int] = {}
    for issue in issues:
        dims[issue.dimension] = dims.get(issue.dimension, 0) + 1
        checks[issue.check] = checks.get(issue.check, 0) + 1
    return dims, checks


def _print_product_section(batch, products):
    """Print the PRODUCT QUALITY section."""
    print()
    print("=" * 64)
    print(f"  1. PRODUCT QUALITY — {len(products)} products")
    print("=" * 64)
    print()
    print(f"  Composite:    {batch.composite:.2f}")
    print(f"  Realism:      {batch.avg_realism:.2f}")
    print(f"  Coherence:    {batch.avg_coherence:.2f}")
    print(f"  Completeness: {batch.avg_completeness:.2f}")
    print(f"  Diversity:    {batch.diversity:.2f}")
    print()

    print("-" * 64)
    print("  PER-PRODUCT SCORES")
    print("-" * 64)
    for ps in batch.product_scores:
        title = ps.title[:40].ljust(40)
        print(f"  {title}  Real={ps.realism:.2f}  Cohr={ps.coherence:.2f}  Cmpl={ps.completeness:.2f}")
    print()

    all_issues = batch.all_issues
    if all_issues:
        print("-" * 64)
        print(f"  PRODUCT ISSUES ({len(all_issues)})")
        print("-" * 64)
        for issue in all_issues:
            dim = issue.dimension.upper()[:5].ljust(5)
            print(f"  [{dim}] {issue.check}: {issue.message}")
        print()

        dims, checks = _issue_summary(all_issues)
        print("-" * 64)
        print("  PRODUCT ISSUE COUNTS BY DIMENSION")
        print("-" * 64)
        for dim, count in sorted(dims.items(), key=lambda x: -x[1]):
            print(f"  {dim:<20} {count}")
        print()
        print("-" * 64)
        print("  PRODUCT ISSUE COUNTS BY CHECK")
        print("-" * 64)
        for check, count in sorted(checks.items(), key=lambda x: -x[1]):
            print(f"  {check:<35} {count}")
        print()
    else:
        print("  No product issues found.")
        print()


def _print_availability_section(avail_batch):
    """Print the AVAILABILITY QUALITY section."""
    total_days = sum(s.day_count for s in avail_batch.option_scores)
    print()
    print("=" * 64)
    print(f"  2. AVAILABILITY QUALITY — {len(avail_batch.option_scores)} options, {total_days} days")
    print("=" * 64)
    print()
    print(f"  Composite:    {avail_batch.composite:.2f}")
    print(f"  Realism:      {avail_batch.avg_realism:.2f}")
    print(f"  Coherence:    {avail_batch.avg_coherence:.2f}")
    print(f"  Completeness: {avail_batch.avg_completeness:.2f}")
    print()

    print("-" * 64)
    print("  PER-OPTION SCORES")
    print("-" * 64)
    for os in avail_batch.option_scores:
        label = f"{os.product_title[:25]}→{os.option_title[:20]}".ljust(48)
        print(
            f"  {label} Real={os.realism:.2f}  Cohr={os.coherence:.2f}"
            f"  Cmpl={os.completeness:.2f}  Days={os.day_count}"
        )
    print()

    avail_issues = avail_batch.all_issues
    if avail_issues:
        print("-" * 64)
        print(f"  AVAILABILITY ISSUES ({len(avail_issues)})")
        print("-" * 64)
        for issue in avail_issues:
            dim = issue.dimension.upper()[:5].ljust(5)
            print(f"  [{dim}] {issue.check}: {issue.message}")
        print()

        dims, checks = _issue_summary(avail_issues)
        print("-" * 64)
        print("  AVAILABILITY ISSUE COUNTS BY DIMENSION")
        print("-" * 64)
        for dim, count in sorted(dims.items(), key=lambda x: -x[1]):
            print(f"  {dim:<20} {count}")
        print()
        print("-" * 64)
        print("  AVAILABILITY ISSUE COUNTS BY CHECK")
        print("-" * 64)
        for check, count in sorted(checks.items(), key=lambda x: -x[1]):
            print(f"  {check:<35} {count}")
        print()
    else:
        print("  No availability issues found.")
        print()


def _print_combined_summary(batch, avail_batch):
    """Print the COMBINED SUMMARY section."""
    print()
    print("=" * 64)
    print("  3. COMBINED SUMMARY")
    print("=" * 64)
    print()

    prod_issues = batch.all_issues
    avail_issues = avail_batch.all_issues if avail_batch else []
    total_issues = len(prod_issues) + len(avail_issues)

    # Weighted average: products and availability contribute equally
    if avail_batch:
        combined_composite = (batch.composite + avail_batch.composite) / 2
        combined_realism = (batch.avg_realism + avail_batch.avg_realism) / 2
        combined_coherence = (batch.avg_coherence + avail_batch.avg_coherence) / 2
        combined_completeness = (batch.avg_completeness + avail_batch.avg_completeness) / 2
    else:
        combined_composite = batch.composite
        combined_realism = batch.avg_realism
        combined_coherence = batch.avg_coherence
        combined_completeness = batch.avg_completeness

    print(f"  {'':30s} {'Products':>10s}  {'Avail':>10s}  {'Combined':>10s}")
    print(f"  {'Composite':<30s} {batch.composite:>10.2f}  ", end="")
    if avail_batch:
        print(f"{avail_batch.composite:>10.2f}  {combined_composite:>10.2f}")
    else:
        print(f"{'—':>10s}  {combined_composite:>10.2f}")

    print(f"  {'Realism':<30s} {batch.avg_realism:>10.2f}  ", end="")
    if avail_batch:
        print(f"{avail_batch.avg_realism:>10.2f}  {combined_realism:>10.2f}")
    else:
        print(f"{'—':>10s}  {combined_realism:>10.2f}")

    print(f"  {'Coherence':<30s} {batch.avg_coherence:>10.2f}  ", end="")
    if avail_batch:
        print(f"{avail_batch.avg_coherence:>10.2f}  {combined_coherence:>10.2f}")
    else:
        print(f"{'—':>10s}  {combined_coherence:>10.2f}")

    print(f"  {'Completeness':<30s} {batch.avg_completeness:>10.2f}  ", end="")
    if avail_batch:
        print(f"{avail_batch.avg_completeness:>10.2f}  {combined_completeness:>10.2f}")
    else:
        print(f"{'—':>10s}  {combined_completeness:>10.2f}")

    print(f"  {'Diversity (products only)':<30s} {batch.diversity:>10.2f}  {'—':>10s}  {batch.diversity:>10.2f}")
    print()
    print(f"  Total issues: {total_issues} (products: {len(prod_issues)}, availability: {len(avail_issues)})")
    print()
    print("=" * 64)


def main():
    save = "--save" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--save"]
    seed_product_file = args[0] if args else _detect_seed_product_file()
    path = Path(seed_product_file)
    if not path.exists():
        print(f"File not found: {path}")
        sys.exit(1)

    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    model = _detect_model()
    products = [Product.model_validate(p) for p in raw]
    scorer = QualityScorer()
    batch = scorer.score_batch(products)

    # Header
    print()
    print("=" * 64)
    print(f"  QUALITY REPORT — {len(products)} products")
    print(f"  Model: {model}")
    print("=" * 64)

    # --- Section 1: Products ---
    _print_product_section(batch, products)

    # --- Section 2: Availability ---
    avail_path = Path(_detect_seed_availability_file())
    avail_batch = None
    if avail_path.exists():
        with open(avail_path, "r", encoding="utf-8") as f:
            availability = json.load(f)

        avail_scorer = AvailabilityQualityScorer()
        avail_batch = avail_scorer.score_batch(availability, raw)
        _print_availability_section(avail_batch)
    else:
        print()
        print("  (No availability data found at %s — skipping)" % avail_path)
        print()

    # --- Section 3: Combined Summary ---
    _print_combined_summary(batch, avail_batch)

    # --- Save to file ---
    if save:
        reports_dir = Path("metrics/quality-reports")
        reports_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        model_slug = model.replace(":", "-").replace("/", "-")
        report_path = reports_dir / f"{ts}_{model_slug}_{len(products)}p.json"

        prod_dims, prod_checks = _issue_summary(batch.all_issues)

        report_data = {
            "timestamp": datetime.now().isoformat(),
            "model": model,
            "product_count": len(products),
            "seed_product_file": str(seed_product_file),
            "products": {
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
                    for i in batch.all_issues
                ],
                "issue_summary": {
                    "by_dimension": prod_dims,
                    "by_check": prod_checks,
                    "total": len(batch.all_issues),
                },
            },
        }

        if avail_batch:
            avail_dims, avail_checks = _issue_summary(avail_batch.all_issues)
            total_days = sum(s.day_count for s in avail_batch.option_scores)
            report_data["availability"] = {
                "option_count": len(avail_batch.option_scores),
                "total_days": total_days,
                "scores": {
                    "composite": round(avail_batch.composite, 3),
                    "realism": round(avail_batch.avg_realism, 3),
                    "coherence": round(avail_batch.avg_coherence, 3),
                    "completeness": round(avail_batch.avg_completeness, 3),
                },
                "per_option": [
                    {
                        "product_id": os.product_id,
                        "option_id": os.option_id,
                        "product_title": os.product_title,
                        "option_title": os.option_title,
                        "realism": round(os.realism, 3),
                        "coherence": round(os.coherence, 3),
                        "completeness": round(os.completeness, 3),
                        "day_count": os.day_count,
                    }
                    for os in avail_batch.option_scores
                ],
                "issues": [
                    {
                        "dimension": i.dimension,
                        "check": i.check,
                        "message": i.message,
                        "product_id": i.product_id,
                        "option_id": i.option_id,
                    }
                    for i in avail_batch.all_issues
                ],
                "issue_summary": {
                    "by_dimension": avail_dims,
                    "by_check": avail_checks,
                    "total": len(avail_batch.all_issues),
                },
            }

        # Combined summary in JSON
        combined_composite = batch.composite
        if avail_batch:
            combined_composite = (batch.composite + avail_batch.composite) / 2
        report_data["combined"] = {
            "composite": round(combined_composite, 3),
            "total_issues": len(batch.all_issues) + (
                len(avail_batch.all_issues) if avail_batch else 0
            ),
        }

        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report_data, f, indent=2)
        print(f"  Saved to: {report_path}")
        print()


if __name__ == "__main__":
    main()
