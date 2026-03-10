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

1. `PromptBuilder` loads 2-3 real product samples from `real-samples/` as few-shot examples, trims them to essential fields, and constructs a prompt with the OCTO JSON schema and generation rules.

2. `OllamaClient` sends the prompt to a local Ollama instance (`/api/generate`). Async, with a 300s timeout.

3. `ProductGenerator` orchestrates the loop:
   - Calls Ollama for each product (up to `max_retries` attempts)
   - Strips markdown code fences from LLM output
   - Parses JSON and validates against the `Product` Pydantic model
   - Assigns fresh UUID v4 values to all ID/reference fields
   - Checks every string field against `RealSamplesIndex` to prevent production data leakage
   - Retries on parse errors, validation failures, or leakage matches
   - Backs off exponentially on connection failures

4. `RealSamplesIndex` builds an O(1) lookup set of all string values from the real sample JSON files. Any generated string (length ≥ 4) that matches a real value triggers a retry.

Products can be saved to disk (`--dump-seed`) and loaded later (`--skip-seed`) to avoid repeated LLM calls.

## Serve phase

Once products are loaded, the `StateManager` generates availability data and the FastAPI server starts.

### State Manager

`StateManager` is the single source of truth. On `load_products()`:

- Validates ID uniqueness (option IDs within product, unit IDs within option)
- Generates calendar entries and availability slots for each product-option pair across `availability_days` (default 90)
- Availability status is randomly assigned with weighted distribution:
  - 60% AVAILABLE, 15% LIMITED, 10% SOLD_OUT, 10% CLOSED, 5% FREESALE
- `START_TIME` products get one slot per `availabilityLocalStartTimes` entry, with vacancies distributed across slots
- `OPENING_HOURS` products get a single all-day slot per day

Data structures:
- `_products`: `dict[product_id, Product]`
- `_calendar`: `dict[(product_id, option_id, date_str), CalendarEntry]`
- `_slots`: `dict[(product_id, option_id, date_str), list[AvailabilitySlot]]`
- `_slots_by_id`: `dict[slot_id, AvailabilitySlot]`

### FastAPI server

The app factory (`create_app`) wires:

- Product routes: `GET /products`, `GET /products/{product_id}`
- Availability routes: `POST /availability/calendar`, `POST /availability`
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
- `CalendarEntry`: per-day summary (available, status, vacancies, capacity)
- `AvailabilitySlot`: per-timeslot detail (start/end times, vacancies, capacity)

All models use Pydantic with `camelCase` aliases for JSON serialization (matching the OCTO spec) and `snake_case` internally.

## Telemetry

See [observability.md](observability.md) for the full metrics catalog and stack setup.
