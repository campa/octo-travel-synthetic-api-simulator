"""Orchestrates Product generation via Ollama with retry and validation.

Calls the LLM, parses JSON output, validates against the Product model,
and assigns fresh UUIDs.
"""

import asyncio
import json
import logging
import random
import re
import time
import uuid

from models.product import Product
from seeder.ollama_client import (
    OllamaClient,
    OllamaInvalidResponseError,
    OllamaUnreachableError,
    SeedingFailedError,
)
from seeder.prompt_builder import PromptBuilder
from seeder.quality import QualityScorer
from telemetry.setup import TelemetryInstruments

logger = logging.getLogger(__name__)

# Regex to strip markdown code fences from LLM output
_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?\s*```$", re.DOTALL)


def _strip_code_fences(text: str) -> str:
    """Remove markdown code fences if present."""
    text = text.strip()
    m = _CODE_FENCE_RE.match(text)
    return m.group(1).strip() if m else text

def _validation_error_to_hint(exc: Exception) -> str:
    """Convert a Pydantic validation error into an LLM-friendly constraint hint.

    Translates technical error messages into plain-English rules the LLM
    can follow on the next attempt.
    """
    from pydantic import ValidationError

    if not isinstance(exc, ValidationError):
        return f"Schema validation failed: {exc}"

    hints: list[str] = []
    for error in exc.errors():
        loc = ".".join(str(p) for p in error["loc"])
        err_type = error["type"]
        msg = error["msg"]
        ctx = error.get("ctx", {})

        if err_type == "greater_than":
            limit = ctx.get("gt", "0")
            hints.append(f"Field '{loc}' must be greater than {limit} (got {error.get('input', '?')})")
        elif err_type == "greater_than_equal":
            limit = ctx.get("ge", "0")
            hints.append(f"Field '{loc}' must be >= {limit}")
        elif err_type == "less_than":
            limit = ctx.get("lt")
            hints.append(f"Field '{loc}' must be less than {limit}")
        elif err_type == "less_than_equal":
            limit = ctx.get("le")
            hints.append(f"Field '{loc}' must be <= {limit}")
        elif err_type == "string_type":
            hints.append(f"Field '{loc}' must be a string")
        elif err_type == "missing":
            hints.append(f"Field '{loc}' is required but was missing")
        elif err_type == "enum":
            expected = ctx.get("expected", "")
            hints.append(f"Field '{loc}' must be one of: {expected}")
        elif "value_error" in err_type or err_type == "value_error":
            hints.append(f"Field '{loc}': {msg}")
        else:
            hints.append(f"Field '{loc}': {msg}")

    return "; ".join(hints) if hints else f"Schema validation failed: {exc}"

_CUTOFF_RE = re.compile(r"^(\d+)\s+(hours?|minutes?|days?)$")

# ---------------------------------------------------------------------------
# Fictional fallback pools for when the LLM omits fields.
# Inspired by real OCTO product patterns but entirely made up.
# ---------------------------------------------------------------------------

_FALLBACK_PRODUCT_NAMES = [
    "City Sightseeing Tour",
    "General Admission",
    "Harbor Cruise 60 Min",
    "Guided Walking Tour Old Town",
    "Hop-On Hop-Off Bus 24h",
    "Sunset Boat Trip",
    "Museum & Gallery Entry",
    "Wine Tasting Experience",
    "Bike Rental Half Day",
    "Airport Transfer Shuttle",
    "River Kayak Adventure",
    "Observation Deck Fast Track",
    "Food & Market Walking Tour",
    "Aquarium Family Pass",
    "Historic Castle Guided Visit",
    "Snorkeling Excursion Morning",
    "Panoramic Cable Car Ride",
    "Cooking Class Traditional Cuisine",
    "Segway City Highlights 2h",
    "Whale Watching Expedition",
]

_FALLBACK_OPTION_NAMES = [
    "Standard",
    "Premium",
    "Morning Departure",
    "Afternoon Session",
    "Full Day Pass",
    "Express Entry",
    "Group Package",
    "Private Tour",
    "Economy",
    "Guided Option",
    "Self-Guided Option",
    "Family Bundle",
    "Sunset Slot",
    "Early Bird",
    "Last Minute",
    "Weekend Special",
]

_FALLBACK_START_TIMES = [
    ["09:00"],
    ["10:00"],
    ["09:00", "14:00"],
    ["08:30", "11:00", "14:30"],
    ["09:00", "10:00", "11:00", "13:00", "14:00", "15:00"],
]


def _normalize_llm_output(data: dict) -> dict:
    """Fix common LLM output quirks before Pydantic validation.

    This keeps the Product model clean (matching the OCTO spec) while
    tolerating predictable LLM mistakes like missing optional fields
    or wrong types for optional values.

    Backfills spec-defined defaults for fields the local LLM commonly omits.
    """
    # --- product-level defaults ---
    data.setdefault("internalName",
                    data.get("title") or random.choice(_FALLBACK_PRODUCT_NAMES))

    for option in data.get("options", []):
        # --- structural option fields the LLM sometimes omits ---
        option.setdefault("default", False)
        option.setdefault("internalName",
                          option.get("title") or random.choice(_FALLBACK_OPTION_NAMES))
        option.setdefault("availabilityLocalStartTimes",
                          [] if data.get("availabilityType") == "OPENING_HOURS"
                          else random.choice(_FALLBACK_START_TIMES))
        option.setdefault("cancellationCutoff", "0 hours")

        # --- cancellation cutoff derivation ---
        cutoff = option.get("cancellationCutoff", "")
        if isinstance(cutoff, str):
            m = _CUTOFF_RE.match(cutoff)
            if m:
                if "cancellationCutoffAmount" not in option:
                    option["cancellationCutoffAmount"] = int(m.group(1))
                if "cancellationCutoffUnit" not in option:
                    option["cancellationCutoffUnit"] = m.group(2).rstrip("s")

        # --- option-level defaults the LLM often omits ---
        option.setdefault("requiredContactFields", [])
        if "restrictions" not in option:
            option["restrictions"] = {"minUnits": 0, "maxUnits": None}
        else:
            option["restrictions"].setdefault("minUnits", 0)
            option["restrictions"].setdefault("maxUnits", None)

        # Coerce durationAmount int -> str (LLM sends 3 instead of "3")
        dur = option.get("durationAmount")
        if dur is not None and not isinstance(dur, str):
            option["durationAmount"] = str(dur)

        for unit in option.get("units", []):
            # --- unit-level defaults the LLM often omits ---
            unit.setdefault("requiredContactFields", [])

            restrictions = unit.get("restrictions")
            if restrictions is None:
                unit["restrictions"] = {
                    "minAge": 0,
                    "maxAge": 0,
                    "idRequired": False,
                    "minQuantity": None,
                    "maxQuantity": None,
                    "paxCount": 1,
                    "accompaniedBy": [],
                }
            else:
                restrictions.setdefault("minAge", 0)
                restrictions.setdefault("maxAge", 0)
                restrictions.setdefault("idRequired", False)
                restrictions.setdefault("minQuantity", None)
                restrictions.setdefault("maxQuantity", None)
                restrictions.setdefault("paxCount", 1)
                restrictions.setdefault("accompaniedBy", [])

    return data




def _assign_fresh_uuids(product: Product) -> Product:
    """Overwrite id fields with fresh UUID v4 values.

    References are set to None — suppliers typically use null or short
    alphanumeric codes, never UUIDs.  Pickup location IDs also get
    fresh UUIDs.
    """
    product.id = str(uuid.uuid4())
    product.reference = None
    for option in product.options:
        option.id = str(uuid.uuid4())
        option.reference = None
        # Pickup location IDs (octo/pickups capability)
        if option.pickup_locations:
            for loc in option.pickup_locations:
                loc.id = str(uuid.uuid4())
        for unit in option.units:
            unit.id = str(uuid.uuid4())
            unit.reference = None
    return product




class ProductGenerator:
    """Generates synthetic OCTO Products via Ollama with retry and validation."""

    def __init__(
        self,
        ollama_client: OllamaClient,
        prompt_builder: PromptBuilder,
        max_retries: int = 3,
        telemetry: TelemetryInstruments | None = None,
    ) -> None:
        self._client = ollama_client
        self._prompt_builder = prompt_builder
        self._max_retries = max_retries
        self._tel = telemetry

    @staticmethod
    def _product_summary(product: Product) -> dict:
        """Extract a compact diversity fingerprint from a generated product."""
        return {
            "title": product.title or product.internal_name,
            "country": product.country,
            "availabilityType": product.availability_type.value,
            "categoryLabels": product.category_labels or [],
        }

    async def generate_products(self, count: int) -> list[Product]:
        """Generate `count` Products via Ollama with retry + validation.

        Raises SeedingFailedError if all retries exhausted for any product.
        """
        gen_start = time.monotonic()
        products: list[Product] = []
        for i in range(count):
            logger.info("Generating product %d/%d", i + 1, count)
            previously_generated = [
                self._product_summary(p) for p in products
            ]
            product = await self._generate_single_product(
                product_num=i + 1,
                previously_generated=previously_generated if previously_generated else None,
            )
            products.append(product)

        gen_duration = time.monotonic() - gen_start
        if self._tel:
            self._tel.seeder_generation_duration_seconds.record(gen_duration)

        # --- Quality scoring ---
        scorer = QualityScorer()
        batch_score = scorer.score_batch(products)
        if self._tel:
            self._tel.quality_score.record(batch_score.composite)
            self._tel.quality_diversity_score.record(batch_score.diversity)
            for ps in batch_score.product_scores:
                attrs = {"product_id": ps.product_id}
                self._tel.quality_realism_score.record(ps.realism, attrs)
                self._tel.quality_coherence_score.record(ps.coherence, attrs)
                self._tel.quality_completeness_score.record(ps.completeness, attrs)
            for issue in batch_score.all_issues:
                self._tel.quality_issues_total.add(
                    1, {"dimension": issue.dimension, "check": issue.check}
                )

        logger.info("Generated %d products", count)
        return products

    async def _generate_single_product(
        self,
        product_num: int,
        previously_generated: list[dict] | None = None,
    ) -> Product:
        """Generate one product with retry logic.

        Raises SeedingFailedError if all retries exhausted.
        """
        error_hints: list[str] = []
        product_start = time.monotonic()

        for attempt in range(1, self._max_retries + 1):
            prompt = self._prompt_builder.build_prompt(
                error_hints=error_hints if error_hints else None,
                previously_generated=previously_generated,
            )
            logger.debug("Product %d, attempt %d — full prompt:\n%s", product_num, attempt, prompt)

            try:
                logger.info(
                    "Product %d: Ollama attempt %d/%d",
                    product_num, attempt, self._max_retries,
                )
                if self._tel:
                    self._tel.seeder_ollama_requests_total.add(1)

                req_start = time.monotonic()
                ollama_resp = await self._client.generate(prompt)
                req_duration = time.monotonic() - req_start

                # Record Ollama request duration
                if self._tel:
                    self._tel.seeder_ollama_request_duration_seconds.record(req_duration)
                    # LLM performance metrics from Ollama response metadata
                    self._tel.llm_total_duration.record(
                        ollama_resp.total_duration / 1e6  # ns → ms
                    )
                    self._tel.llm_generation_duration.record(
                        ollama_resp.eval_duration / 1e6  # ns → ms
                    )
                    if ollama_resp.eval_duration > 0:
                        tps = ollama_resp.eval_count / ollama_resp.eval_duration * 1e9
                        self._tel.llm_tokens_per_second.record(tps)
                    self._tel.llm_prompt_tokens.add(ollama_resp.prompt_eval_count)
                    self._tel.llm_completion_tokens.add(ollama_resp.eval_count)

                raw_text = _strip_code_fences(ollama_resp.response)

                # Parse JSON
                try:
                    data = json.loads(raw_text)
                except json.JSONDecodeError as exc:
                    error_hints.append(
                        f"Output was not valid JSON (parse error at position {exc.pos}). "
                        "Return ONLY a raw JSON object, no markdown fences or extra text."
                    )
                    raise OllamaInvalidResponseError(
                        f"Failed to parse LLM output as JSON: {exc}"
                    ) from exc

                # Validate against Product model
                try:
                    data = _normalize_llm_output(data)
                    product = Product.model_validate(data)
                except Exception as exc:
                    error_hints.append(
                        _validation_error_to_hint(exc)
                    )
                    raise OllamaInvalidResponseError(
                        f"LLM output does not conform to Product schema: {exc}"
                    ) from exc

                # Assign fresh UUIDs
                product = _assign_fresh_uuids(product)

                # Success
                product_duration = time.monotonic() - product_start
                if self._tel:
                    self._tel.seeder_products_generated_total.add(1)
                    self._tel.product_attempts.record(
                        attempt, {"product_id": product.id}
                    )
                    self._tel.product_generation_duration_seconds.record(
                        product_duration, {"product_id": product.id}
                    )
                    self._tel.product_options_count.add(
                        len(product.options), {"product_id": product.id}
                    )
                    for option in product.options:
                        self._tel.product_units_count.add(
                            len(option.units),
                            {"product_id": product.id, "option_id": option.id},
                        )

                logger.info("Product %d: validated successfully", product_num)
                return product

            except OllamaUnreachableError:
                logger.error(
                    "Product %d: Ollama unreachable on attempt %d/%d",
                    product_num, attempt, self._max_retries,
                )
                if self._tel:
                    self._tel.seeder_ollama_errors_total.add(
                        1, {"error_type": "UNREACHABLE"}
                    )
                    self._tel.seeder_errors_total.add(
                        1, {"error_type": "OLLAMA_UNREACHABLE"}
                    )
                if attempt < self._max_retries:
                    if self._tel:
                        self._tel.seeder_ollama_retries_total.add(1)
                    backoff = 2 ** attempt
                    logger.info("Backing off %d seconds before retry", backoff)
                    await asyncio.sleep(backoff)

            except OllamaInvalidResponseError as exc:
                logger.error(
                    "Product %d: invalid response on attempt %d/%d — %s",
                    product_num, attempt, self._max_retries, exc,
                )
                if self._tel:
                    self._tel.seeder_ollama_errors_total.add(
                        1, {"error_type": "INVALID_RESPONSE"}
                    )
                    self._tel.seeder_errors_total.add(
                        1, {"error_type": "OLLAMA_INVALID_RESPONSE"}
                    )
                if attempt < self._max_retries and self._tel:
                    self._tel.seeder_ollama_retries_total.add(1)
                # Immediate retry (no backoff)

        if self._tel:
            self._tel.seeder_products_failed_total.add(1)

        logger.error(
            "Product %d: all %d attempts exhausted", product_num, self._max_retries
        )
        raise SeedingFailedError(
            f"Failed to generate product {product_num} after {self._max_retries} attempts"
        )
