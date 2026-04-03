# Data Quality

OTAS includes a deterministic quality scoring system that evaluates generated OCTO products across four dimensions: realism, coherence, completeness, and diversity. The scorer runs automatically after each batch generation and emits metrics via OpenTelemetry. A CLI report script is also available for ad-hoc analysis.

- [Quick start](#quick-start)
- [Scoring dimensions](#scoring-dimensions)
  - [Realism](#realism-per-product)
  - [Coherence](#coherence-per-product)
  - [Completeness](#completeness-per-product)
  - [Diversity](#diversity-batch-level)
- [Composite score](#composite-score)
- [Integration with the generator](#integration-with-the-generator)
- [OpenTelemetry metrics](#opentelemetry-metrics)
- [Issue tracking](#issue-tracking)
- [Comparing runs](#comparing-runs)
- [Example: 50-product batch with nemotron-3-nano:30b](#example-50-product-batch-with-nemotron-3-nano30b)
- [Limitations](#limitations)

## Quick start

```bash
# Run against the current seed file
python scripts/quality_report.py

# Run and save results for later comparison
python scripts/quality_report.py --save

# Run against a specific file
python scripts/quality_report.py path/to/other_seed.json --save
```

Saved reports go to `metrics/quality-reports/` with the model name and timestamp in the filename (e.g., `20260403-170701_nemotron-3-nano-30b_10p.json`).

## Scoring dimensions

All per-product scores range from 0.0 (worst) to 1.0 (best).

### Realism (per-product)

Detects patterns that would never appear in real supplier data. The score is `checks_passed / checks_total` — each check is binary pass/fail.

| Check | What it detects | Pass condition |
|-------|----------------|----------------|
| Coordinate entropy | Placeholder coordinates like `48.111111` | Shannon entropy of decimal digits ≥ 1.5 for both lat and lon |
| Sequential digits | Artificial patterns like `48.123456` | Decimal digits don't form a sequential pattern (constant diff between consecutive digits) |
| City centroid | Generic center-of-city coordinates (e.g., `51.5074, -0.1278` for London) | Coordinates don't match any of ~10 known city centroids within 0.001° tolerance |
| Google Place ID uniqueness | Same Place ID reused across unrelated products | Place ID not shared with another product in the batch |
| Known dummy Place ID | LLM-memorized Place IDs (e.g., Sydney Opera House `ChIJN1t_tDeuEmsRUsoyG83frY4`) | Place ID not in the blocklist of ~10 known LLM favorites |
| Currency ↔ Country | USD on a German product, EUR on a US product | Currency matches the expected set for the product's country code |
| Timezone ↔ Country | `Europe/London` on a US product | Timezone prefix matches the expected pattern for the country |
| ADULT age range | `minAge=0, maxAge=0` on ADULT when sibling CHILD has real age ranges | If any CHILD unit in the same option has meaningful age ranges, ADULT must too |
| Child price ≤ Adult | CHILD unit priced higher than ADULT in the same option | Max CHILD price ≤ min ADULT price |

Not all checks apply to every product. If a product has no locations, coordinate checks are skipped. If it has no pricing, price checks are skipped. Skipped checks don't affect the denominator.

**Example:** A product with 8 applicable checks, 6 passing → realism = 0.75.

### Coherence (per-product)

Checks internal consistency between related fields within a single product. Same scoring method: `checks_passed / checks_total`.

| Check | What it detects | Pass condition |
|-------|----------------|----------------|
| Duration alignment | Option says "90 minutes" but product range is 150–180 | Option `durationAmount` (converted to minutes) falls within `[durationMinutesFrom, durationMinutesTo]` |
| Contact-delivery consistency | Description says "delivered via email" but no option requires `emailAddress` | If description mentions email delivery, at least one option has `emailAddress` in `requiredContactFields` |
| FAQ-pricing contradiction | FAQ says "children under 12 join for free" but CHILD unit is priced | If a FAQ mentions free entry for an age group, no priced CHILD unit covers that age range |
| Option default logic | Multiple options marked as `default: true` | At most one option has `default: true` |
| Cancellation cutoff consistency | `cancellationCutoff: "48 hours"` but `cancellationCutoffAmount: 24` | Parsed amount from the cutoff string matches `cancellationCutoffAmount` |

Checks that don't apply (e.g., no FAQs, no duration fields) are excluded from the denominator so they don't penalize the score.

**Example:** A product with 4 applicable checks, 3 passing → coherence = 0.75.

### Completeness (per-product)

Measures presence of optional-but-valuable fields using weighted scoring. Each field has a weight reflecting its importance for a realistic product.

| Field | Weight | Present when |
|-------|--------|-------------|
| `description` (HTML body) | 2 | Non-empty string with > 10 characters |
| `features` | 1 | At least one feature object |
| `faqs` | 1 | At least one FAQ object |
| `media` with COVER | 2 | At least one media item with `rel: "COVER"` |
| `locations` | 2 | At least one location object |
| `categoryLabels` | 1 | At least one label |
| `commentary` | 1 | At least one commentary object |
| Option `shortDescription` | 1 | All options have a non-empty `shortDescription` |

Score = `sum(weight × present) / sum(all weights)`.

**Example:** A product missing `faqs` (weight 1) and `commentary` (weight 1) → score = (11 - 1 - 1) / 11 = 9/11 = 0.82.

### Diversity (batch-level)

Measured across the entire batch, not per-product. Averages five sub-scores:

| Dimension | Calculation | Perfect score means |
|-----------|-------------|-------------------|
| Country spread | `unique_countries / product_count` | Every product in a different country |
| Title uniqueness | `unique_titles / product_count` | No duplicate product titles |
| Availability type balance | `min_type_count / max_type_count` | Roughly equal split between START_TIME and OPENING_HOURS |
| Category spread | `unique_categories / total_category_assignments` | Wide variety of category labels |
| Currency spread | `unique_currencies / product_count` | Not all products using the same currency |

Diversity = average of the five sub-scores.

**Example:** 10 products, 4 unique countries, 7 unique titles, 4 START_TIME + 1 OPENING_HOURS → country=0.40, titles=0.70, avail=0.25, ... → diversity ≈ 0.47.

## Composite score

The composite score is a weighted average of all four dimensions:

```
composite = 0.30 × realism + 0.30 × coherence + 0.20 × completeness + 0.20 × diversity
```

Realism and coherence are weighted higher (30% each) because they represent the issues most likely to confuse downstream mapping systems. Completeness and diversity get 20% each.

## Integration with the generator

The quality scorer runs automatically at the end of `ProductGenerator.generate_products()`. It:

1. Scores the entire batch across all four dimensions
2. Emits per-product scores and batch-level diversity via OpenTelemetry
3. Logs a summary line via the `seeder.quality` logger
4. Counts individual issues with `dimension` and `check` attributes for filtering

No extra configuration is needed. Quality metrics flow to OpenObserve alongside the existing seeder and LLM performance metrics.

## OpenTelemetry metrics

All quality metrics use the `otas_quality_` prefix. See [observability.md](observability.md) for the full metrics catalog.

| Metric | Type | Scope |
|--------|------|-------|
| `otas_quality_score` | Histogram | Composite score per batch |
| `otas_quality_realism_score` | Histogram | Per product, with `product_id` attribute |
| `otas_quality_coherence_score` | Histogram | Per product, with `product_id` attribute |
| `otas_quality_completeness_score` | Histogram | Per product, with `product_id` attribute |
| `otas_quality_diversity_score` | Histogram | Per batch |
| `otas_quality_issues_total` | Counter | Per issue, with `dimension` and `check` attributes |

## Issue tracking

Every quality problem found is recorded as a `QualityIssue` with:

- `dimension` — which scoring dimension it belongs to (realism, coherence, completeness, diversity)
- `check` — the specific check that failed (e.g., `city_centroid`, `duration_mismatch`)
- `message` — human-readable description of the problem
- `product_id` — which product it applies to (empty for batch-level issues)

The CLI report groups issues by dimension and by check type, making it easy to identify the most frequent problems across a batch.

## Comparing runs

Use `--save` to persist reports as JSON files in `metrics/quality-reports/`. Each file includes:

- Model name (auto-detected from `.env`)
- Timestamp
- All scores (composite, per-dimension, per-product)
- Full issue list with counts

This allows comparing quality across different models, temperatures, or prompt changes. File naming convention: `{timestamp}_{model}_{product_count}p.json`.

## Example: 50-product batch with nemotron-3-nano:30b

The following results were collected from a 50-product generation run using `nemotron-3-nano:30b` at temperature 0.5 on an Apple M3 Max (64 GB RAM). This serves as a reference baseline for the current generator and prompt configuration.

### Summary scores

| Dimension | Score |
|-----------|-------|
| Composite | 0.74 |
| Realism | 0.72 |
| Coherence | 0.94 |
| Completeness | 1.00 |
| Diversity | 0.19 |

### Issue distribution

148 total issues across 50 products:

| Dimension | Count | % of total |
|-----------|-------|------------|
| Realism | 135 | 91% |
| Coherence | 10 | 7% |
| Diversity | 3 | 2% |

### Top issue types

| Check | Count | Description |
|-------|-------|-------------|
| `adult_age_range` | 41 | ADULT units with minAge=0/maxAge=0 while sibling CHILD has real ages |
| `known_dummy_place_id` | 34 | Sydney Opera House Place ID reused across products |
| `coordinate_entropy` | 31 | Low-entropy coordinates (placeholder-like patterns) |
| `currency_country_mismatch` | 10 | Wrong currency for the product's country (e.g., EUR for AE) |
| `duration_mismatch` | 10 | Option duration outside product's durationMinutesFrom/To range |
| `timezone_country_mismatch` | 7 | Wrong timezone prefix for the country |
| `city_centroid` | 6 | Coordinates matching known city centroids |
| `duplicate_place_id` | 6 | Same Place ID reused across different products |

### Key observations

- Completeness is perfect (1.00) — the model consistently generates all optional content fields (descriptions, FAQs, media, locations).
- Coherence is strong (0.94) — duration mismatches are the main issue, occurring in ~20% of products that have option-level durations.
- Realism (0.72) is dominated by three recurring patterns: ADULT age ranges (82% of products), dummy Place IDs (68%), and low-entropy coordinates (62%). These are inherent LLM limitations, not prompt issues.
- Diversity collapses at scale (0.19) — only 6 unique countries and 15 unique titles across 50 products. The LLM starts recycling titles around product 10-15 despite the diversity steering prompt. This is the highest-impact area for improvement.

The full report is saved at `metrics/quality-reports/20260403-180029_nemotron-3-nano-30b_50p.json`.

## Limitations

The current scorer is fully deterministic — no LLM calls. This means it catches format-level issues (placeholder coordinates, dummy IDs, mathematical mismatches) but cannot evaluate:

- Whether an activity name is plausible for a given city
- Whether a price is realistic for the market
- Whether a description narratively matches the product title
- Subtle semantic contradictions beyond FAQ-vs-pricing

These would require either embedding-based similarity checks or an LLM-as-judge pass, which are planned as future enhancements.
