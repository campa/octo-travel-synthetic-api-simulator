# Data Quality

OTAS includes a deterministic quality scoring system that evaluates generated data across two domains:

- **Product quality** — scores products, options, and units across realism, coherence, completeness, and diversity
- **Availability quality** — scores availability calendar data across realism, coherence, and completeness

Both run automatically after generation and emit metrics via OpenTelemetry. A CLI report script produces a three-section output: products, availability, and a combined summary.

- [Data Quality](#data-quality)
  - [Quick start](#quick-start)
  - [Product scoring dimensions](#product-scoring-dimensions)
    - [Realism (per-product)](#realism-per-product)
    - [Coherence (per-product)](#coherence-per-product)
    - [Completeness (per-product)](#completeness-per-product)
    - [Diversity (batch-level)](#diversity-batch-level)
  - [Product composite score](#product-composite-score)
  - [Availability scoring dimensions](#availability-scoring-dimensions)
    - [Realism (per-option)](#realism-per-option)
    - [Coherence (per-option)](#coherence-per-option)
    - [Completeness (per-option)](#completeness-per-option)
  - [Availability composite score](#availability-composite-score)
  - [Availability coherence checks (inline)](#availability-coherence-checks-inline)
  - [Integration with the generators](#integration-with-the-generators)
  - [OpenTelemetry metrics](#opentelemetry-metrics)
  - [Issue tracking](#issue-tracking)
  - [Comparing runs](#comparing-runs)
  - [Example: 10-product batch with nemotron-3-nano:30b](#example-10-product-batch-with-nemotron-3-nano30b)
    - [Product scores](#product-scores)
    - [Availability scores](#availability-scores)
    - [Combined summary](#combined-summary)
    - [Key observations](#key-observations)
  - [Comparison across batch sizes](#comparison-across-batch-sizes)
  - [Limitations](#limitations)

## Quick start

```bash
# Run against the current seed files (products + availability)
python scripts/quality_report.py

# Run and save results for later comparison
python scripts/quality_report.py --save

# Run against a specific product file
python scripts/quality_report.py path/to/other_seed.json --save
```

The report automatically picks up the availability file from `data/seed_availability_data.json` (or the path configured in `.env`). Saved reports go to `metrics/quality-reports/`.

## Product scoring dimensions

All per-product scores range from 0.0 (worst) to 1.0 (best).

### Realism (per-product)

Detects patterns that would never appear in real supplier data. Score = `checks_passed / checks_total`.

| Check | What it detects | Pass condition |
|-------|----------------|----------------|
| Coordinate entropy | Placeholder coordinates like `48.111111` | Shannon entropy of decimal digits ≥ 1.5 for both lat and lon |
| Sequential digits | Artificial patterns like `48.123456` | Decimal digits don't form a sequential pattern |
| City centroid | Generic center-of-city coordinates (e.g., `51.5074, -0.1278` for London) | Coordinates don't match known city centroids within 0.001° |
| Google Place ID uniqueness | Same Place ID reused across unrelated products | Place ID not shared with another product |
| Known dummy Place ID | LLM-memorized Place IDs (e.g., Sydney Opera House) | Place ID not in the blocklist |
| Currency ↔ Country | USD on a German product, EUR on a US product | Currency matches expected set for the country |
| Timezone ↔ Country | `Europe/London` on a US product | Timezone prefix matches expected pattern |
| ADULT age range | `minAge=0, maxAge=0` on ADULT when sibling CHILD has real ages | ADULT has meaningful age ranges when CHILD does |
| Child price ≤ Adult | CHILD unit priced higher than ADULT | Max CHILD price ≤ min ADULT price |

### Coherence (per-product)

Checks internal consistency between related fields. Score = `checks_passed / checks_total`.

| Check | What it detects | Pass condition |
|-------|----------------|----------------|
| Duration alignment | Option says "90 minutes" but product range is 150–180 | Option duration within `[durationMinutesFrom, durationMinutesTo]` |
| Contact-delivery consistency | Description says "delivered via email" but no option requires `emailAddress` | At least one option has `emailAddress` if description mentions email delivery |
| FAQ-pricing contradiction | FAQ says "children under 12 join for free" but CHILD unit is priced | No priced CHILD unit covers the free age range |
| Option default logic | Multiple options marked as `default: true` | At most one option has `default: true` |
| Cancellation cutoff consistency | `cancellationCutoff: "48 hours"` but `cancellationCutoffAmount: 24` | Parsed amount matches `cancellationCutoffAmount` |

### Completeness (per-product)

Measures presence of optional-but-valuable fields using weighted scoring.

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

### Diversity (batch-level)

Measured across the entire batch. Averages five sub-scores:

| Dimension | Calculation | Perfect score means |
|-----------|-------------|-------------------|
| Country spread | `unique_countries / product_count` | Every product in a different country |
| Title uniqueness | `unique_titles / product_count` | No duplicate product titles |
| Description uniqueness | `unique_descriptions / description_count` | No duplicate descriptions |
| Availability type balance | `min_type_count / max_type_count` | Equal split between START_TIME and OPENING_HOURS |
| Category spread | `unique_categories / total_category_assignments` | Wide variety of category labels |
| Currency spread | `unique_currencies / product_count` | Not all products using the same currency |

## Product composite score

```
composite = 0.30 × realism + 0.30 × coherence + 0.20 × completeness + 0.20 × diversity
```

Realism and coherence are weighted higher because they represent issues most likely to confuse downstream mapping systems.

## Availability scoring dimensions

All per-option scores range from 0.0 (worst) to 1.0 (best).

### Realism (per-option)

Detects patterns that wouldn't appear in a real booking system.

| Check | What it detects | Penalty |
|-------|----------------|---------|
| Monotonous status | All days have identical status (e.g., all AVAILABLE) | -0.15 |
| Monotonous vacancies | All open days have identical vacancy count | -0.20 |
| Vacancies > capacity | Vacancies exceeding capacity on any day | -0.15 |
| Invalid capacity | Zero or negative capacity on open days | -0.10 |

### Coherence (per-option)

Checks consistency between availability data and the product definition.

| Check | What it detects | Penalty |
|-------|----------------|---------|
| Invalid freesale | FREESALE status when `allowFreesale=false` | -0.15 |
| Opening hours with start times | `availabilityLocalStartTimes` on an OPENING_HOURS product | -0.10 |
| Invalid start times | Start times not in the option's defined list | -0.10 per time (max -0.30) |
| Overlapping start times | Sessions that overlap given the option's duration | -0.10 per pair (max -0.30) |

### Completeness (per-option)

Checks that availability data has the expected fields populated.

| Check | What it detects | Penalty |
|-------|----------------|---------|
| No days | Zero availability days generated | Score = 0.0 |
| Missing capacity | >50% of open days missing capacity | -0.15 |
| Missing vacancies | >50% of open days missing vacancies (non-FREESALE) | -0.15 |
| Missing opening hours | >50% of open OPENING_HOURS days missing openingHours | -0.15 |

## Availability composite score

```
composite = 0.40 × realism + 0.40 × coherence + 0.20 × completeness
```

Realism and coherence are weighted equally at 40% each since both are critical for a realistic availability calendar.

## Availability coherence checks (inline)

In addition to the quality scorer, the availability generation pipeline includes inline coherence validation after each chunk. These checks auto-fix structural issues before the data is stored.

| Check | What it catches | Auto-fix |
|-------|----------------|----------|
| Start time subset | LLM invented start times not in the option's defined list | Filters to allowed subset |
| Time overlap | Two start times that overlap given the option's duration | Removes overlapping times (greedy, keeps earliest) |
| FREESALE on non-freesale product | FREESALE status when `allowFreesale=false` | Flips to AVAILABLE |
| Vacancies > capacity | Vacancies exceeding capacity | Caps vacancies to capacity |
| Start times on OPENING_HOURS | `availabilityLocalStartTimes` on an OPENING_HOURS product | Strips the field |
| Start times on closed days | Start times on CLOSED or SOLD_OUT days | Strips the field |

Issues are also fed back as error hints for the next retry attempt, so the LLM learns to avoid them. After coherence fixes, a slot cap enforces `max_slots_per_week` by trimming start times or closing excess days.

## Integration with the generators

The product quality scorer runs automatically at the end of `ProductGenerator.generate_products()`. The availability quality scorer is available via the CLI report script. Both:

1. Score across all applicable dimensions
2. Emit per-entity scores and batch-level metrics via OpenTelemetry
3. Log summary lines via their respective loggers
4. Count individual issues with `dimension` and `check` attributes for filtering

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

Every quality problem is recorded as an issue with:

- `dimension` — which scoring dimension (realism, coherence, completeness, diversity)
- `check` — the specific check that failed (e.g., `city_centroid`, `monotonous_vacancies`)
- `message` — human-readable description
- `product_id` — which product (empty for batch-level issues)
- `option_id` — which option (availability issues only)

The CLI report groups issues by dimension and check type for both products and availability separately.

## Comparing runs

Use `--save` to persist reports as JSON in `metrics/quality-reports/`. Each file includes:

- Model name (auto-detected from `.env`)
- Timestamp
- `products` section: scores, per-product breakdown, issues
- `availability` section: scores, per-option breakdown, issues
- `combined` section: overall composite and total issue count

File naming convention: `{timestamp}_{model}_{product_count}p.json`.

## Example: 10-product batch with nemotron-3-nano:30b

Results from a 10-product generation run with 14-day availability window using `nemotron-3-nano:30b` at temperature 0.5 on an Apple M3 Max (64 GB RAM). This serves as the current reference baseline.

### Product scores

| Dimension | Score |
|-----------|-------|
| Composite | 0.81 |
| Realism | 0.72 |
| Coherence | 0.94 |
| Completeness | 0.98 |
| Diversity | 0.59 |

39 total product issues across 10 products:

| Dimension | Count | % of total |
|-----------|-------|------------|
| Realism | 34 | 87% |
| Coherence | 3 | 8% |
| Diversity | 1 | 3% |
| Completeness | 1 | 3% |

Top product issue types:

| Check | Count | Description |
|-------|-------|-------------|
| `adult_age_range` | 11 | ADULT units with minAge=0/maxAge=0 while sibling CHILD has real ages |
| `coordinate_entropy` | 7 | Low-entropy coordinates (placeholder-like patterns) |
| `duplicate_place_id` | 7 | Same Place ID reused across different products |
| `known_dummy_place_id` | 6 | Sydney Opera House Place ID reused across products |
| `city_centroid` | 3 | Coordinates matching known city centroids |
| `duration_mismatch` | 3 | Option duration outside product's durationMinutesFrom/To range |

### Availability scores

16 options, 80 total calendar days (5 days per option from 14-day window with 3-day chunks):

| Dimension | Score |
|-----------|-------|
| Composite | 0.89 |
| Realism | 0.73 |
| Coherence | 1.00 |
| Completeness | 1.00 |

25 total availability issues:

| Check | Count | Description |
|-------|-------|-------------|
| `monotonous_status` | 15 | All days have identical status (AVAILABLE) |
| `monotonous_vacancies` | 10 | All open days have identical vacancies (12) |

### Combined summary

| Metric | Products | Availability | Combined |
|--------|----------|--------------|----------|
| Composite | 0.81 | 0.89 | 0.85 |
| Realism | 0.72 | 0.73 | 0.73 |
| Coherence | 0.94 | 1.00 | 0.97 |
| Completeness | 0.98 | 1.00 | 0.99 |
| Diversity | 0.59 | — | 0.59 |
| Total issues | 39 | 25 | 64 |

### Key observations

- **Product completeness is near-perfect** (0.98) — the model consistently generates all optional content fields (descriptions, FAQs, media, locations) including the octo/content capability fields (title, description, features at product, option, and unit levels).
- **Product coherence is strong** (0.94) — duration mismatches are the main issue.
- **Product realism** (0.72) is dominated by three recurring LLM patterns: ADULT age ranges (11 issues), low-entropy/dummy coordinates (13 issues), and duplicate Place IDs (7 issues).
- **Availability coherence is perfect** (1.00) — all start times are valid subsets, no overlaps, correct OPENING_HOURS handling. The inline coherence checks and auto-fixes are effective.
- **Availability realism is the weakest area** (0.73) — nemotron consistently generates monotonous availability: all days `AVAILABLE` with identical `vacancies=12`. The prompt instructs it to vary, but the model doesn't comply. This is a known model limitation.
- **Diversity** (0.59) is reasonable for 10 products but degrades at larger batch sizes as the LLM starts recycling titles and countries.

The full report is saved at `metrics/quality-reports/20260424-174930_nemotron-3-nano-30b_10p.json`.

## Comparison across batch sizes

Historical product quality scores across different batch sizes (all with nemotron-3-nano:30b):

| Metric | 10 products | 30 products | 50 products |
|--------|-------------|-------------|-------------|
| Composite | 0.81 | 0.78 | 0.74 |
| Realism | 0.72 | 0.70 | 0.72 |
| Coherence | 0.94 | 0.99 | 0.94 |
| Completeness | 0.98 | 0.99 | 1.00 |
| Diversity | 0.59 | 0.35 | 0.19 |
| Total issues | 39 | 91 | 148 |

Diversity degrades with batch size — the LLM exhausts its variety around 10-15 products. Realism and coherence remain stable regardless of batch size.

## Limitations

The current scorer is fully deterministic — no LLM calls. This means it catches format-level issues (placeholder coordinates, dummy IDs, mathematical mismatches) but cannot evaluate:

- Whether an activity name is plausible for a given city
- Whether a price is realistic for the market
- Whether a description narratively matches the product title
- Subtle semantic contradictions beyond FAQ-vs-pricing
- Whether availability patterns match real-world seasonal demand

These would require either embedding-based similarity checks or an LLM-as-judge pass, which are planned as future enhancements.
