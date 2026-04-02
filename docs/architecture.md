# Architecture

## Overview

OTAS is a stateful mock server with two phases: seed and serve.

```
┌─────────────────────────────────────────────────────────┐
│                      CLI (cli.py)                       │
│  parse args → init telemetry → seed or load → serve     │
└──────────┬──────────────────────────────────┬───────────┘
           │                                  │
     ┌─────▼──────┐                    ┌──────▼──────┐
     │   Seeder    │                   │  FastAPI     │
     │  Pipeline   │                   │  Server      │
     └─────┬──────┘                    └──────┬──────┘
           │                                  │
     ┌─────▼──────┐                    ┌──────▼──────┐
     │   Ollama    │                   │   State      │
     │   (LLM)    │                   │   Manager    │
     └────────────┘                    └─────────────┘
```

## Seed phase

On startup (unless `--skip-seed`), the seeder pipeline generates synthetic OCTO products:

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

## Serve phase

Once products are loaded into the `StateManager`, the FastAPI server starts.

### State Manager

`StateManager` is the single source of truth for product data. On `load_products()`:

- Validates ID uniqueness (option IDs within product, unit IDs within option)
- Stores products in an in-memory dictionary keyed by product ID

Data structures:
- `_products`: `dict[product_id, Product]`

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

All models use Pydantic with `camelCase` aliases for JSON serialization (matching the OCTO spec) and `snake_case` internally.

## Telemetry

See [observability.md](observability.md) for the full metrics catalog and stack setup.
