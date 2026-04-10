# Architecture

## Overview

OTAS is a stateful mock server with two phases: seed and serve. The seed phase itself has two sequential stages — product generation and availability generation — where availability depends on the product data.

```
                         CLI (cli.py)
                  parse args → init telemetry
                             │
              ┌──────────────▼──────────────┐
              │     SEED PHASE (sequential)  │
              │                              │
              │  ┌────────────────────────┐  │
              │  │ 1. Product Generation  │  │
              │  │    PromptBuilder       │  │
              │  │    ProductGenerator    │──┼──→ Ollama (LLM)
              │  │    QualityScorer       │  │
              │  └───────────┬────────────┘  │
              │              │ products       │
              │              ▼               │
              │  ┌────────────────────────┐  │
              │  │ 2. Availability Gen.   │  │
              │  │    reads product data  │  │
              │  │    AvailabilityPrompt  │──┼──→ Ollama (LLM)
              │  │    AvailabilityGen.    │  │
              │  │    coherence checks    │  │
              │  └───────────┬────────────┘  │
              │              │ availability   │
              └──────────────┼──────────────┘
                             │
                             ▼
              ┌──────────────────────────────┐
              │        State Manager         │
              │  _products + _availability   │
              └──────────────┬──────────────┘
                             │
                             ▼
              ┌──────────────────────────────┐
              │       FastAPI Server         │
              │  GET /products, POST /avail  │
              └──────────────────────────────┘
```

The two stages can run together in one CLI invocation or separately:

```bash
# Everything in one run
uv run otas --dump-seed

# Or split into two runs
uv run otas --dump-seed --skip-availability   # 1. products only
uv run otas --skip-seed --dump-seed           # 2. load products, generate availability
```

## Seed phase

The seed phase has two sequential stages. Products are always generated (or loaded) first, then availability is generated using the product data as input.

### Stage 1: Product generation

On startup (unless `--skip-seed`), the product seeder generates synthetic OCTO products:

1. `PromptBuilder` constructs a prompt with the OCTO JSON schema (loaded from `octo-std/` spec files) and generation rules.

2. `OllamaClient` sends the prompt to a local Ollama instance (`/api/generate`). Async, with a 300s timeout.

3. `ProductGenerator` orchestrates the loop:
   - Calls Ollama for each product (up to `max_retries` attempts)
   - Strips markdown code fences from LLM output
   - Normalizes common LLM quirks (missing optional fields, wrong types)
   - Parses JSON and validates against the `Product` Pydantic model
   - Assigns fresh UUID v4 values to all ID/reference fields
   - Backs off exponentially on connection failures

4. `QualityScorer` runs after the batch is complete, scoring all products across realism, coherence, completeness, and diversity. See [data-quality.md](data-quality.md).

Products can be saved to disk (`--dump-seed`) and loaded later (`--skip-seed`) to avoid repeated LLM calls.

### Stage 2: Availability generation

After products are generated (or loaded), the availability seeder generates calendar data for each product+option pair. This stage can be skipped with `--skip-availability`.

#### Weekly chunking

The total availability window (`availability_window_days`) is split into 7-day chunks. Each chunk is one LLM call → validate → coherence fix → slot cap cycle:

```
availability_window_days = 21, start_date = 2026-04-10

Week 1: 2026-04-10 → 7 days → LLM call → validate → coherence fix → cap slots → append
Week 2: 2026-04-17 → 7 days → LLM call → validate → coherence fix → cap slots → append
Week 3: 2026-04-24 → 7 days → LLM call → validate → coherence fix → cap slots → append

Result: 21 validated + coherent + capped calendar days
```

This keeps the LLM output small (7 JSON objects max per call), reduces validation errors, and allows per-week slot budget enforcement.

#### Product-aware prompt

The availability prompt includes full product context so the LLM generates coherent data:

- Product title, country, timezone, categories, description
- Option start times, duration, and unit types
- `availabilityType` (START_TIME vs OPENING_HOURS) — determines whether days have start times or opening hours
- `allowFreesale` — controls whether FREESALE status is permitted

For START_TIME products, the prompt explicitly tells the LLM that `availabilityLocalStartTimes` must be a subset of the option's defined start times, and that times must not overlap given the option's duration.

#### Coherence validation

After Pydantic schema validation, a coherence check runs against the product data:

| Check | What it catches | Auto-fix |
|-------|----------------|----------|
| Start time subset | LLM invented times not in the option's list | Filters to allowed subset |
| Time overlap | Two start times that overlap given the duration (e.g. 09:00 + 2h overlaps 10:00) | Removes overlapping times (greedy, keeps earliest) |
| FREESALE on non-freesale product | FREESALE status when `allowFreesale=false` | Flips to AVAILABLE |
| Start times on OPENING_HOURS | `availabilityLocalStartTimes` present on an OPENING_HOURS product | Strips the field |
| Start times on closed days | Start times on CLOSED or SOLD_OUT days | Strips the field |

Coherence issues are also fed back as error hints for the next retry attempt, following the same dynamic prompt pattern as product generation.

#### Slot capping

After coherence fixes, `_cap_slots()` enforces the `max_slots_per_week` budget. A "slot" is:

- For START_TIME products: one entry in `availabilityLocalStartTimes` (a day with 3 start times = 3 slots)
- For OPENING_HOURS products: one open day = 1 slot

The algorithm walks days in order with a budget counter. When a day would exceed the remaining budget, it first tries to trim the start times list. If even 1 slot doesn't fit, the entire day is flipped to CLOSED.

#### Retry and error hints

Same pattern as product generation: if validation or JSON parsing fails, error hints accumulate and are injected into the next attempt's prompt. The LLM sees what went wrong and avoids repeating the same mistakes.

Both product and availability seed data can be saved with `--dump-seed` and loaded with `--skip-seed` to avoid repeated LLM calls.

### Dynamic prompt construction

The prompt is not static — it adapts based on context from previous attempts and previously generated products. This is a key mechanism for improving output quality without increasing model size.

```
Product 1, attempt 1:
  prompt = schema + generation rules

Product 1, attempt 2 (after validation failure):
  prompt = schema + generation rules + error hints from attempt 1

Product 2, attempt 1:
  prompt = schema + generation rules + summary of Product 1

Product 3, attempt 1:
  prompt = schema + generation rules + summaries of Products 1 & 2
```

#### Error hint feedback

When an Ollama attempt fails validation (malformed JSON, Pydantic schema violation), the generator extracts a human-readable error description and appends it to the next attempt's prompt under a `## Previous Attempt Errors — MUST FIX` section. This gives the LLM explicit instructions on what went wrong, significantly reducing repeated mistakes.

For example, if the LLM returns `cancellationCutoff: "hour"` (missing the amount), the next prompt includes:

```
## Previous Attempt Errors — MUST FIX
Your previous attempts were rejected due to the following validation errors.
You MUST avoid these mistakes:
1. cancellationCutoff must match '{amount} {unit}(s)' (e.g. '24 hours'), got 'hour'
```

Error hints accumulate across retries — attempt 3 sees hints from both attempt 1 and 2.

#### Diversity steering

When generating multiple products in a batch, each product after the first receives a summary of all previously generated products. The summary includes title, country, availability type, and category labels. The prompt instructs the LLM to generate a different type of activity, in a different country if possible, and to vary pricing and availability patterns.

This prevents the common LLM tendency to generate near-identical products in a batch (e.g., five "Historic Castle Tours" in London).

#### Prompt size growth

Because each product's prompt includes summaries of all previously generated products, the prompt size grows linearly with the batch. Measured with `nemotron-3-nano:30b` generating 10 products:

| Product # | Prompt tokens (approx) | Growth reason |
|-----------|----------------------|---------------|
| 1 | ~4,500 | Schema + generation rules only |
| 2 | ~4,900 | + 1 product summary |
| 3 | ~5,200 | + 2 product summaries |
| 5 | ~5,500 | + 4 product summaries |
| 10 | ~12,000 | + 9 product summaries |

```
Prompt tokens per product (10-product batch)

 12K ┤                                                          ╭─
     │                                                     ╭────╯
 10K ┤                                                ╭────╯
     │                                           ╭────╯
  8K ┤                                      ╭────╯
     │                                 ╭────╯
  6K ┤                            ╭────╯
     │                  ╭────────╯
  5K ┤        ╭────────╯
     │   ╭────╯
  4K ┤───╯
     └─────┬─────┬─────┬─────┬─────┬─────┬─────┬─────┬─────┬─────
           1     2     3     4     5     6     7     8     9    10
                              Product #
```

Total prompt tokens for a 10-product batch: ~106,000 (cumulative across all calls). The base prompt (schema + rules) is ~4,500 tokens. Each product summary adds ~200-400 tokens to subsequent prompts.

For larger batches (50 products), the final prompts can reach ~15,000+ tokens. This is well within the context window of `nemotron-3-nano:30b` (128K) but worth monitoring — the `otas_llm_prompt_tokens` OTel counter tracks this in real time.

## Serve phase

Once products are loaded into the `StateManager`, the FastAPI server starts.

### State Manager

`StateManager` is the single source of truth for product and availability data.

On `load_products()`:
- Validates ID uniqueness (option IDs within product, unit IDs within option)
- Stores products in an in-memory dictionary keyed by product ID

On `load_availability()`:
- Stores availability calendar days in a nested dictionary: `product_id → option_id → [calendar days]`

Data structures:
- `_products`: `dict[product_id, Product]`
- `_availability`: `dict[product_id, dict[option_id, list[dict]]]`

### FastAPI server

The app factory (`create_app`) wires:

- Product routes: `GET /products`, `GET /products/{product_id}`
- `RequestMetricsMiddleware`: tracks request count, duration, errors, and per-product hits
- Global exception handler: returns structured JSON errors with `X-Error-Id` correlation headers
- Content-Type middleware: ensures all responses are `application/json`

### Error handling

All errors return a consistent JSON body:

```json
{
  "error": "INVALID_PRODUCT_ID",
  "errorMessage": "The productId was missing or invalid",
  "errorId": "uuid-v4"
}
```

Response headers include `X-Error-Id` and `X-Error-Code` for correlation. Error details are logged in a structured, grep-friendly format.

## Data model

Follows the OCTO standard. Key entities:

- `Product` → has many `Option` → has many `Unit`
- `AvailabilityCalendarDay` → one per date per product+option, with `OpeningHours` entries

All models use Pydantic with `camelCase` aliases for JSON serialization (matching the OCTO spec) and `snake_case` internally.

The `AvailabilityCalendarDay` model validates:
- Status ↔ available ↔ vacancies consistency (e.g. CLOSED must have `available=false`)
- Date format (ISO 8601 YYYY-MM-DD)
- Time format for opening hours (HH:MM)

## Telemetry

See [observability.md](observability.md) for the full metrics catalog and stack setup.
