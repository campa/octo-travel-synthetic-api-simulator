# OCTO Travel Synthetic API Simulator (OTAS)

A stateful mock server that implements the [OCTO](https://www.octo.travel/) travel API standard using LLM-generated synthetic data. Built for dev, testing, and demo environments where you need realistic OCTO-compliant responses without connecting to a real supplier.

## What it does

1. Uses a local LLM (Ollama) to generate realistic tour/activity products
2. Builds 90 days of availability data (calendar + time slots) in memory
3. Serves it all through OCTO-compliant REST endpoints
4. Validates that no real production data leaks into synthetic output

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
ollama pull qwen3:14b
uv sync
uv run otas
```

The server starts on `http://localhost:8080` by default. On first run it calls Ollama to generate 10 products, then serves them.

Any Ollama model works. Change it with `OTAS_OLLAMA_MODEL` or via `.env`.

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
| `POST` | `/availability/calendar` | Calendar availability (date range) |
| `POST` | `/availability` | Slot availability (by date range or IDs) |

These follow the [OCTO API specification](https://docs.octo.travel). The full OpenAPI spec is in `api-spec/octo-spec.yaml`.

### Example requests

```bash
# List products
curl http://localhost:8080/products

# Get a specific product
curl http://localhost:8080/products/<product-id>

# Check calendar availability
curl -X POST http://localhost:8080/availability/calendar \
  -H "Content-Type: application/json" \
  -d '{"productId": "<id>", "optionId": "<id>", "localDateStart": "2026-03-01", "localDateEnd": "2026-03-31"}'

# Check slot availability
curl -X POST http://localhost:8080/availability \
  -H "Content-Type: application/json" \
  -d '{"productId": "<id>", "optionId": "<id>", "localDateStart": "2026-03-01", "localDateEnd": "2026-03-07"}'
```

## CLI options

```
uv run otas [OPTIONS]

--host              Bind host (default: 0.0.0.0)
--port              Bind port (default: 8080)
--product-count     Number of products to generate (default: 10)
--max-retries       Max Ollama retries per product (default: 3)
--availability-days Days of availability to generate (default: 90)
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
| `OTAS_OLLAMA_MODEL` | `qwen3:14b` | Ollama model name |
| `OTAS_PRODUCT_COUNT` | `10` | Products to generate |
| `OTAS_MAX_RETRIES` | `3` | Max retries per product |
| `OTAS_AVAILABILITY_DAYS` | `90` | Days of availability data |
| `OTAS_AVG_SLOTS_PER_DAY` | `3` | Avg time slots for START_TIME products |
| `OTAS_OTLP_ENDPOINT` | `localhost:5081` | OTLP gRPC endpoint |
| `OTAS_OTLP_USER` | `admin@otas.local` | OpenObserve basic auth user |
| `OTAS_OTLP_PASSWORD` | `admin` | OpenObserve basic auth password |
| `OTAS_SERVICE_NAME` | `otas` | OTel service name |
| `OTAS_SEED_FILE` | `seed_data.json` | Seed data file path |

## Observability

Metrics are exported via OpenTelemetry (OTLP gRPC) to an optional OpenObserve stack:

```bash
# Start the observability stack (requires Docker)
docker compose -f metrics/docker-compose.otel.yml up -d

# OpenObserve UI: http://localhost:5080 (admin@otas.local / admin)
```

The app works fine without the observability stack. Telemetry is fire-and-forget.

See [docs/observability.md](docs/observability.md) for details.

## Architecture

See [docs/architecture.md](docs/architecture.md) for the full system design.

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
├── models/             # Pydantic models (Product, Availability, Errors)
├── seeder/             # LLM-based product generation pipeline
│   ├── generator.py    # Orchestrates generation with retry + validation
│   ├── ollama_client.py # Async Ollama HTTP client
│   ├── prompt_builder.py # Few-shot prompt construction
│   └── sample_index.py # Production data leakage detection
├── server/             # FastAPI app, routes, middleware, error handling
│   ├── app.py          # App factory
│   ├── routes/         # Product and availability endpoints
│   ├── middleware.py   # Request metrics middleware
│   └── error_handler.py # Structured error responses with correlation IDs
├── state/manager.py    # In-memory state store + availability generation
└── telemetry/setup.py  # OpenTelemetry SDK init + metric instruments
```

## License

Apache 2.0. See [LICENSE](LICENSE).
