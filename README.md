# OCTO Travel Synthetic API Simulator (OTAS)

A stateful mock server that implements the [OCTO](https://www.octo.travel/) travel API standard using LLM-generated synthetic data. Built for dev, testing, and demo environments where you need realistic OCTO-compliant responses without connecting to a live supplier.

## Table of contents

- [OCTO Travel Synthetic API Simulator (OTAS)](#octo-travel-synthetic-api-simulator-otas)
  - [Table of contents](#table-of-contents)
  - [What it does](#what-it-does)
  - [Prerequisites](#prerequisites)
  - [Quick start](#quick-start)
    - [Choosing a model](#choosing-a-model)
  - [API endpoints](#api-endpoints)
    - [Example requests](#example-requests)
  - [CLI options](#cli-options)
  - [Configuration](#configuration)
    - [Per-module log levels](#per-module-log-levels)
  - [Observability](#observability)
  - [Data quality](#data-quality)
  - [Generation performance](#generation-performance)
  - [Architecture](#architecture)
  - [Documentation](#documentation)
  - [Development](#development)
  - [Project structure](#project-structure)
  - [License](#license)

## What it does

1. Uses a local LLM (Ollama) to generate realistic tour/activity products
2. Serves them through OCTO-compliant REST endpoints

## Prerequisites

| Software | Version | Notes |
|----------|---------|-------|
| Python | 3.10+ | |
| [uv](https://docs.astral.sh/uv/) | 0.4+ | Python package manager |
| [Ollama](https://ollama.ai/) | latest | Local LLM runtime (needed for seeding) |
| Docker | latest | Optional, for observability stack |

## Quick start

```bash
git clone <repo-url>
cd octo-travel-synthetic-api-simulator
ollama pull nemotron-3-nano:30b
uv sync
uv run otas
```

The server starts on `http://localhost:8080` by default. On first run it calls Ollama to generate 10 products, then serves them.

Swagger UI is available at `http://localhost:8080/docs`, ReDoc at `http://localhost:8080/redoc`.

### Choosing a model

The recommended model is `nemotron-3-nano:30b` — it produces high-quality OCTO-compliant JSON with good structural completeness and coherence. Any Ollama-compatible model works though. Configure it via `.env` or environment variable:

```bash
# .env
OTAS_OLLAMA_MODEL=nemotron-3-nano:30b
```

Larger models may improve diversity and realism but require more VRAM/RAM. Smaller models (14B and below) are faster but may need more retries.

To skip LLM generation and use a cached seed file:

```bash
# First run: generate and save
uv run otas --dump-seed

# Subsequent runs: load from file (fast, no Ollama needed)
uv run otas --skip-seed
```

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/products` | List all products |
| `GET` | `/products/{productId}` | Get a single product |

These follow the [OCTO API specification](https://docs.octo.travel). The full OpenAPI spec is in `api-spec/octo-spec.yaml`.

### Example requests

```bash
# List products
curl http://localhost:8080/products

# Get a specific product
curl http://localhost:8080/products/<product-id>
```

## CLI options

```
uv run otas [OPTIONS]

--host              Bind host (default: 0.0.0.0)
--port              Bind port (default: 8080)
--product-count     Number of products to generate (default: 10)
--max-retries       Max Ollama retries per product (default: 3)
--avg-slots-per-day Avg time slots per day for START_TIME products (default: 3)
--seed-file         Path to seed data JSON file (default: seed_data.json)
--skip-seed         Load from seed file instead of calling Ollama
--dump-seed         Save generated data to seed file after generation
```

## Configuration

All settings use the `OTAS_` environment variable prefix and can also be set via a `.env` file. CLI arguments override environment variables.

| Variable | Default | Description |
|----------|---------|-------------|
| `OTAS_HOST` | `0.0.0.0` | Server bind host |
| `OTAS_PORT` | `8080` | Server bind port |
| `OTAS_OLLAMA_URL` | `http://localhost:11434` | Ollama server URL |
| `OTAS_OLLAMA_MODEL` | `nemotron-3-nano:30b` | Ollama model name (any Ollama-compatible model works) |
| `OTAS_PRODUCT_COUNT` | `10` | Products to generate |
| `OTAS_MAX_RETRIES` | `3` | Max retries per product |
| `OTAS_AVG_SLOTS_PER_DAY` | `3` | Avg time slots for START_TIME products |
| `OTAS_OTLP_ENDPOINT` | `localhost:5081` | OTLP gRPC endpoint |
| `OTAS_OTLP_USER` | `admin@otas.local` | OpenObserve basic auth user |
| `OTAS_OTLP_PASSWORD` | `admin` | OpenObserve basic auth password |
| `OTAS_SERVICE_NAME` | `otas` | OTel service name |
| `OTAS_SEED_FILE` | `seed_data.json` | Seed data file path |
| `OTAS_LOG_LEVEL` | `INFO` | Root log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

### Per-module log levels

Logging follows a hierarchical model (like log4j). Each module has its own logger, and you can override the level individually. When unset, the module inherits the root `OTAS_LOG_LEVEL`.

| Variable | Module |
|----------|--------|
| `OTAS_LOG_LEVEL_SEEDER_GENERATOR` | `seeder.generator` — generation orchestration, retry logic, full prompt |
| `OTAS_LOG_LEVEL_SEEDER_PROMPT_BUILDER` | `seeder.prompt_builder` — prompt construction |
| `OTAS_LOG_LEVEL_SEEDER_OLLAMA_CLIENT` | `seeder.ollama_client` — HTTP calls to Ollama |
| `OTAS_LOG_LEVEL_SERVER_APP` | `server.app` — FastAPI app factory |
| `OTAS_LOG_LEVEL_SERVER_MIDDLEWARE` | `server.middleware` — request metrics middleware |
| `OTAS_LOG_LEVEL_STATE_MANAGER` | `state.manager` — in-memory product store |
| `OTAS_LOG_LEVEL_TELEMETRY_SETUP` | `telemetry.setup` — OpenTelemetry init |

Example `.env` to debug only the seeder prompt:

```bash
OTAS_LOG_LEVEL=INFO
OTAS_LOG_LEVEL_SEEDER_GENERATOR=DEBUG
```

This logs the full LLM prompt on each attempt (including error hints from previous failures) while keeping everything else at INFO.

## Observability

Metrics are exported via OpenTelemetry (OTLP gRPC) to an optional OpenObserve stack:

```bash
# Start the observability stack (requires Docker)
docker compose -f metrics/docker-compose.otel.yml up -d

# OpenObserve UI: http://localhost:5080 (admin@otas.local / admin)
```

The app works fine without the observability stack. Telemetry is fire-and-forget.

See [docs/observability.md](docs/observability.md) for details.

## Data quality

Generated products are automatically scored across four dimensions after each batch:

- **Realism** — coordinates, place IDs, currency/country/timezone consistency, age ranges
- **Coherence** — duration alignment, FAQ-vs-pricing consistency, contact field logic
- **Completeness** — presence of descriptions, features, FAQs, media, locations
- **Diversity** (batch-level) — country spread, title uniqueness, availability type balance

Run the quality report manually:

```bash
# Print report to terminal
python scripts/quality_report.py

# Save results for comparison across models/runs
python scripts/quality_report.py --save
```

Saved reports go to `metrics/quality-reports/` with model name and timestamp.

See [docs/data-quality.md](docs/data-quality.md) for the full scoring methodology.

## Generation performance

Data generation is LLM-bound and depends on your hardware and model. Each product requires one Ollama call (more if retries are needed due to validation errors or malformed JSON).

Reference benchmark generating 50 products with `nemotron-3-nano:30b` on an Apple M3 Max (64 GB RAM):

| Metric | Value |
|--------|-------|
| Products requested | 50 |
| Avg time per product | ~30-35s |
| Retries (out of 50) | 1 (invalid IANA timezone `Europe/Riyadh`, fixed on attempt 2) |
| First-attempt success rate | 98% |
| Estimated total time (50 products) | ~25-30 min |

The error hint feedback mechanism resolves most validation issues on the next attempt. Common retry causes: invalid timezones, malformed JSON.

To avoid regenerating every time, use `--dump-seed` on the first run and `--skip-seed` afterwards.

## Architecture

See [docs/architecture.md](docs/architecture.md) for the full system design.

## Documentation

| Document | Description |
|----------|-------------|
| [docs/architecture.md](docs/architecture.md) | System design, seed/serve phases, data model |
| [docs/observability.md](docs/observability.md) | OTel metrics catalog, OpenObserve setup, logging config |
| [docs/data-quality.md](docs/data-quality.md) | Quality scoring methodology, check definitions, composite formula |
| [api-spec/octo-spec.yaml](api-spec/octo-spec.yaml) | OpenAPI specification for the OCTO endpoints |

## Development

```bash
uv sync --group dev          # Install dev dependencies
uv run pytest                # Run tests
uv run ruff check . --fix    # Lint
uv run ruff format .         # Format
uv run mypy src/             # Type check
```

CI runs on GitHub Actions (push/PR to `main`).

## Project structure

```
src/
├── cli.py              # CLI entrypoint, arg parsing, server startup
├── common/config.py    # Pydantic Settings (OTAS_ env prefix)
├── models/             # Pydantic models (Product, Errors)
├── seeder/             # LLM-based product generation pipeline
│   ├── generator.py    # Orchestrates generation with retry + validation
│   ├── ollama_client.py # Async Ollama HTTP client
│   ├── prompt_builder.py # Prompt construction from OCTO spec
│   └── quality.py      # Deterministic quality scoring (realism, coherence, completeness, diversity)
├── server/             # FastAPI app, routes, middleware, error handling
│   ├── app.py          # App factory
│   ├── routes/         # Product endpoints
│   ├── middleware.py   # Request metrics middleware
│   └── error_handler.py # Structured error responses with correlation IDs
├── state/manager.py    # In-memory product store
└── telemetry/setup.py  # OpenTelemetry SDK init + metric instruments
```

## License

Apache 2.0. See [LICENSE](LICENSE).
