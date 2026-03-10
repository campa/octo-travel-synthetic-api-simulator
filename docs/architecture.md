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
   - Parses JSON and validates against the `Product` Pydantic model
   - Assigns fresh UUID v4 values to all ID/reference fields
   - Collects validation error hints and feeds them back into the prompt on retries
   - Backs off exponentially on connection failures

Products can be saved to disk (`--dump-seed`) and loaded later (`--skip-seed`) to avoid repeated LLM calls.

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
