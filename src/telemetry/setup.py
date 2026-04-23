"""OpenTelemetry SDK initialization: metrics + logs.

Initializes the OTel SDK with OTLP gRPC exporters for both metrics and logs,
then returns a TelemetryInstruments dataclass holding all metric instruments.
Logs are sent to OpenObserve via OTLP while still appearing on stdout.
Fire-and-forget: silently ignores export failures so the app continues normally.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from opentelemetry import metrics
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource

from common.config import Settings

logger = logging.getLogger("otas.telemetry")


@dataclass
class TelemetryInstruments:
    """Holds all OTel metric instruments used across the application."""

    # ------------------------------------------------------------------
    # Seeder counters
    # ------------------------------------------------------------------
    seeder_products_generated_total: metrics.Counter = field(init=False)
    seeder_products_failed_total: metrics.Counter = field(init=False)
    seeder_ollama_requests_total: metrics.Counter = field(init=False)
    seeder_ollama_retries_total: metrics.Counter = field(init=False)
    seeder_errors_total: metrics.Counter = field(init=False)
    seeder_ollama_errors_total: metrics.Counter = field(init=False)

    # Seeder histograms
    seeder_ollama_request_duration_seconds: metrics.Histogram = field(init=False)
    seeder_generation_duration_seconds: metrics.Histogram = field(init=False)

    # ------------------------------------------------------------------
    # LLM performance
    # ------------------------------------------------------------------
    llm_total_duration: metrics.Histogram = field(init=False)
    llm_generation_duration: metrics.Histogram = field(init=False)
    llm_tokens_per_second: metrics.Histogram = field(init=False)
    llm_prompt_tokens: metrics.Counter = field(init=False)
    llm_prompt_token_count: metrics.Gauge = field(init=False)
    llm_completion_tokens: metrics.Counter = field(init=False)

    # ------------------------------------------------------------------
    # Server counters
    # ------------------------------------------------------------------
    server_requests_total: metrics.Counter = field(init=False)
    server_errors_total: metrics.Counter = field(init=False)
    server_5xx_errors_total: metrics.Counter = field(init=False)

    # Server histogram
    server_request_duration_seconds: metrics.Histogram = field(init=False)

    # Server gauges (UpDownCounter for gauge semantics)
    server_products_count: metrics.UpDownCounter = field(init=False)

    # ------------------------------------------------------------------
    # Product gauges
    # ------------------------------------------------------------------
    product_options_count: metrics.UpDownCounter = field(init=False)
    product_units_count: metrics.UpDownCounter = field(init=False)

    # Product counters
    product_requests_total: metrics.Counter = field(init=False)

    # Product histogram
    product_generation_duration_seconds: metrics.Histogram = field(init=False)
    product_attempts: metrics.Histogram = field(init=False)

    # ------------------------------------------------------------------
    # Availability metrics
    # ------------------------------------------------------------------
    availability_generation_duration_seconds: metrics.Histogram = field(init=False)
    availability_option_duration_seconds: metrics.Histogram = field(init=False)
    availability_option_attempts: metrics.Histogram = field(init=False)

    # ------------------------------------------------------------------
    # Quality metrics
    # ------------------------------------------------------------------
    quality_score: metrics.Histogram = field(init=False)
    quality_realism_score: metrics.Histogram = field(init=False)
    quality_coherence_score: metrics.Histogram = field(init=False)
    quality_completeness_score: metrics.Histogram = field(init=False)
    quality_diversity_score: metrics.Histogram = field(init=False)
    quality_issues_total: metrics.Counter = field(init=False)


def _create_instruments(meter: metrics.Meter) -> TelemetryInstruments:
    """Create all metric instruments from a meter and populate the dataclass."""
    t = TelemetryInstruments()

    # --- Seeder counters ---
    t.seeder_products_generated_total = meter.create_counter(
        "otas_seeder_products_generated_total",
        description="Total products successfully generated",
    )
    t.seeder_products_failed_total = meter.create_counter(
        "otas_seeder_products_failed_total",
        description="Total products that failed generation",
    )
    t.seeder_ollama_requests_total = meter.create_counter(
        "otas_seeder_ollama_requests_total",
        description="Total requests sent to Ollama",
    )
    t.seeder_ollama_retries_total = meter.create_counter(
        "otas_seeder_ollama_retries_total",
        description="Total Ollama request retries",
    )
    t.seeder_errors_total = meter.create_counter(
        "otas_seeder_errors_total",
        description="Total seeder errors by type",
    )
    t.seeder_ollama_errors_total = meter.create_counter(
        "otas_seeder_ollama_errors_total",
        description="Total Ollama-related errors by type",
    )

    # --- Seeder histograms ---
    t.seeder_ollama_request_duration_seconds = meter.create_histogram(
        "otas_seeder_ollama_request_duration_seconds",
        description="Duration of individual Ollama requests in seconds",
        unit="s",
    )
    t.seeder_generation_duration_seconds = meter.create_histogram(
        "otas_seeder_generation_duration_seconds",
        description="Total seeding phase duration in seconds",
        unit="s",
    )

    # --- LLM performance ---
    t.llm_total_duration = meter.create_histogram(
        "otas_llm_total_duration",
        description="Total Ollama request-response time in milliseconds",
        unit="ms",
    )
    t.llm_generation_duration = meter.create_histogram(
        "otas_llm_generation_duration",
        description="Ollama token generation time in milliseconds",
        unit="ms",
    )
    t.llm_tokens_per_second = meter.create_histogram(
        "otas_llm_tokens_per_second",
        description="Ollama generation speed in tokens/second",
    )
    t.llm_prompt_tokens = meter.create_counter(
        "otas_llm_prompt_tokens",
        description="Total prompt tokens sent to Ollama",
    )
    t.llm_prompt_token_count = meter.create_gauge(
        "otas_llm_prompt_token_count",
        description="Prompt tokens for the last Ollama request",
        unit="tokens",
    )
    t.llm_completion_tokens = meter.create_counter(
        "otas_llm_completion_tokens",
        description="Total completion tokens generated by Ollama",
    )

    # --- Server counters ---
    t.server_requests_total = meter.create_counter(
        "otas_server_requests_total",
        description="Total HTTP requests processed",
    )
    t.server_errors_total = meter.create_counter(
        "otas_server_errors_total",
        description="Total HTTP 4xx/5xx error responses",
    )
    t.server_5xx_errors_total = meter.create_counter(
        "otas_server_5xx_errors_total",
        description="Total HTTP 5xx error responses",
    )

    # --- Server histogram ---
    t.server_request_duration_seconds = meter.create_histogram(
        "otas_server_request_duration_seconds",
        description="HTTP request duration in seconds",
        unit="s",
    )

    # --- Server gauges (UpDownCounter for gauge semantics) ---
    t.server_products_count = meter.create_up_down_counter(
        "otas_server_products_count",
        description="Current number of products in state manager",
    )

    # --- Product gauges ---
    t.product_options_count = meter.create_up_down_counter(
        "otas_product_options_count",
        description="Number of options per product",
    )
    t.product_units_count = meter.create_up_down_counter(
        "otas_product_units_count",
        description="Number of units per product-option",
    )

    # --- Product counters ---
    t.product_requests_total = meter.create_counter(
        "otas_product_requests_total",
        description="Total requests referencing a specific product",
    )

    # --- Product histogram ---
    t.product_generation_duration_seconds = meter.create_histogram(
        "otas_product_generation_duration_seconds",
        description="Duration to generate a single product via Ollama",
        unit="s",
    )
    t.product_attempts = meter.create_histogram(
        "otas_product_attempts",
        description="Number of Ollama attempts needed per product",
    )

    # --- Availability metrics ---
    t.availability_generation_duration_seconds = meter.create_histogram(
        "otas_availability_generation_duration_seconds",
        description="Total availability generation phase duration in seconds",
        unit="s",
    )
    t.availability_option_duration_seconds = meter.create_histogram(
        "otas_availability_option_duration_seconds",
        description="Duration to generate availability for a single product-option",
        unit="s",
    )
    t.availability_option_attempts = meter.create_histogram(
        "otas_availability_option_attempts",
        description="Ollama attempts needed per availability option chunk",
    )

    # --- Quality metrics ---
    t.quality_score = meter.create_histogram(
        "otas_quality_score",
        description="Composite quality score per batch (0.0-1.0)",
    )
    t.quality_realism_score = meter.create_histogram(
        "otas_quality_realism_score",
        description="Realism score per product (0.0-1.0)",
    )
    t.quality_coherence_score = meter.create_histogram(
        "otas_quality_coherence_score",
        description="Coherence score per product (0.0-1.0)",
    )
    t.quality_completeness_score = meter.create_histogram(
        "otas_quality_completeness_score",
        description="Completeness score per product (0.0-1.0)",
    )
    t.quality_diversity_score = meter.create_histogram(
        "otas_quality_diversity_score",
        description="Diversity score per batch (0.0-1.0)",
    )
    t.quality_issues_total = meter.create_counter(
        "otas_quality_issues_total",
        description="Total quality issues found (by dimension and check)",
    )

    return t


def init_telemetry(settings: Settings) -> TelemetryInstruments:
    """Initialize OTel SDK, OTLP gRPC exporters for metrics + logs.

    Fire-and-forget: silently ignores export failures.
    """
    try:
        import base64
        import subprocess

        # Grab git commit SHA for metric labeling
        try:
            commit_sha = subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()
        except Exception:
            commit_sha = "unknown"

        resource = Resource.create({
            "service.name": settings.service_name,
            "service.version": commit_sha,
            "llm.model": settings.ollama_model,
            "llm.temperature": settings.ollama_temperature,
        })
        # OpenObserve requires basic auth on OTLP gRPC
        credentials = base64.b64encode(
            f"{settings.otlp_user}:{settings.otlp_password}".encode()
        ).decode()
        common_headers = (
            ("authorization", f"Basic {credentials}"),
            ("organization", "default"),
        )

        # --- Metrics exporter ---
        metric_exporter = OTLPMetricExporter(
            endpoint=settings.otlp_endpoint,
            insecure=True,
            headers=common_headers,
        )
        reader = PeriodicExportingMetricReader(
            metric_exporter,
            export_interval_millis=10_000,
        )
        provider = MeterProvider(resource=resource, metric_readers=[reader])
        metrics.set_meter_provider(provider)

        # --- Log exporter ---
        log_exporter = OTLPLogExporter(
            endpoint=settings.otlp_endpoint,
            insecure=True,
            headers=common_headers,
        )
        log_provider = LoggerProvider(resource=resource)
        log_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))
        otel_handler = LoggingHandler(
            level=logging.INFO,
            logger_provider=log_provider,
        )
        # Attach to root logger so all app logs flow to OpenObserve
        logging.getLogger().addHandler(otel_handler)

        # Register shutdown hooks so metrics/logs flush before process exit
        import atexit

        def _flush_telemetry():
            try:
                provider.shutdown(timeout_millis=5_000)
            except Exception:
                pass
            try:
                log_provider.shutdown()
            except Exception:
                pass

        atexit.register(_flush_telemetry)

    except Exception:
        logger.warning("Failed to initialize OTel exporter; telemetry disabled")

    meter = metrics.get_meter("otas")
    return _create_instruments(meter)


# Singleton for no-op usage when telemetry is not explicitly initialized
_noop_instruments: TelemetryInstruments | None = None


def get_noop_instruments() -> TelemetryInstruments:
    """Return instruments backed by the default no-op meter (for testing)."""
    global _noop_instruments
    if _noop_instruments is None:
        meter = metrics.get_meter("otas-noop")
        _noop_instruments = _create_instruments(meter)
    return _noop_instruments
