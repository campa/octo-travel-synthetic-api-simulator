# Observability

OTAS exports metrics via OpenTelemetry OTLP gRPC to an optional OpenObserve instance.

## Design principle

Fire-and-forget. The app works identically with or without the observability stack. No errors or warnings if the collector is unavailable.

## Stack

```
OTAS app  ──OTLP gRPC (port 5081)──▶  OpenObserve
                                        (metrics, logs, traces)
```

OpenObserve is a single container that replaces Prometheus + Grafana + Loki. It has native OTLP ingestion, built-in dashboards, and SQL-based querying.

## Setup

```bash
# Start
docker compose -f metrics/docker-compose.otel.yml up -d

# Stop
docker compose -f metrics/docker-compose.otel.yml down

# Remove all data
docker compose -f metrics/docker-compose.otel.yml down -v
```

| Service | URL | Credentials |
|---------|-----|-------------|
| OpenObserve UI | http://localhost:5080 | admin@otas.local / admin |
| OTLP gRPC | localhost:5081 | — |

## Metrics catalog

All metrics use the `otas_` prefix.

### Seeder metrics

| Metric | Type | Description |
|--------|------|-------------|
| `otas_seeder_products_generated_total` | Counter | Products successfully generated |
| `otas_seeder_products_failed_total` | Counter | Products that failed all retries |
| `otas_seeder_ollama_requests_total` | Counter | Total requests to Ollama |
| `otas_seeder_ollama_retries_total` | Counter | Ollama request retries |
| `otas_seeder_errors_total` | Counter | Seeder errors (by `error_type`) |
| `otas_seeder_ollama_errors_total` | Counter | Ollama errors (by `error_type`) |
| `otas_seeder_ollama_request_duration_seconds` | Histogram | Individual Ollama request duration |
| `otas_seeder_generation_duration_seconds` | Histogram | Total seeding phase duration |

### LLM performance metrics

| Metric | Type | Description |
|--------|------|-------------|
| `otas_llm_total_duration` | Histogram | Ollama request-response time (ms) |
| `otas_llm_generation_duration` | Histogram | Token generation time (ms) |
| `otas_llm_tokens_per_second` | Histogram | Generation speed (tokens/sec) |
| `otas_llm_prompt_tokens` | Counter | Total prompt tokens sent |
| `otas_llm_completion_tokens` | Counter | Total completion tokens generated |

### Server metrics

| Metric | Type | Description |
|--------|------|-------------|
| `otas_server_requests_total` | Counter | HTTP requests (by `endpoint`, `status_code`) |
| `otas_server_errors_total` | Counter | 4xx/5xx errors (by `endpoint`, `status_code`, `error_type`) |
| `otas_server_5xx_errors_total` | Counter | 5xx errors (by `endpoint`) |
| `otas_server_request_duration_seconds` | Histogram | Request duration (by `endpoint`) |
| `otas_server_products_count` | Gauge | Current product count in state |

### Per-product metrics

| Metric | Type | Description |
|--------|------|-------------|
| `otas_product_options_count` | Gauge | Options per product |
| `otas_product_units_count` | Gauge | Units per product-option |
| `otas_product_requests_total` | Counter | Requests per product |
| `otas_product_generation_duration_seconds` | Histogram | Single product generation time |

## Configuration

### Logging

OTAS uses Python's hierarchical logging (similar to log4j). Each module has its own named logger, and levels can be set independently via environment variables or `.env`.

The root level is controlled by `OTAS_LOG_LEVEL` (default: `INFO`). Per-module overrides use the pattern `OTAS_LOG_LEVEL_<MODULE>` and take precedence over the root level. When unset, a module inherits the root level.

Available module loggers:

| Variable | Logger name | What it logs at DEBUG |
|----------|-------------|----------------------|
| `OTAS_LOG_LEVEL_SEEDER_GENERATOR` | `seeder.generator` | Full LLM prompt (including error hints), validation details |
| `OTAS_LOG_LEVEL_SEEDER_PROMPT_BUILDER` | `seeder.prompt_builder` | Prompt assembly |
| `OTAS_LOG_LEVEL_SEEDER_OLLAMA_CLIENT` | `seeder.ollama_client` | Raw HTTP request/response to Ollama |
| `OTAS_LOG_LEVEL_SERVER_APP` | `server.app` | App factory, route registration |
| `OTAS_LOG_LEVEL_SERVER_MIDDLEWARE` | `server.middleware` | Per-request metrics details |
| `OTAS_LOG_LEVEL_STATE_MANAGER` | `state.manager` | Product store operations |
| `OTAS_LOG_LEVEL_TELEMETRY_SETUP` | `telemetry.setup` | OTel SDK initialization |

Example — see the full prompt sent to Ollama without other noise:

```bash
# .env
OTAS_LOG_LEVEL=INFO
OTAS_LOG_LEVEL_SEEDER_GENERATOR=DEBUG
```

### OTLP

The OTLP endpoint and auth are configured via:

```bash
OTAS_OTLP_ENDPOINT=localhost:5081
OTAS_OTLP_USER=admin@otas.local
OTAS_OTLP_PASSWORD=admin
```

OpenObserve requires basic auth on its OTLP gRPC endpoint. The defaults match the docker-compose credentials. The exporter uses insecure gRPC (no TLS) and exports every 10 seconds.
