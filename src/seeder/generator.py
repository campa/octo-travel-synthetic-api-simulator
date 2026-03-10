"""Orchestrates Product generation via Ollama with retry and validation.

Calls the LLM, parses JSON output, validates against the Product model,
and assigns fresh UUIDs.
"""

import asyncio
import json
import logging
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



def _assign_fresh_uuids(product: Product) -> Product:
    """Overwrite all id and reference fields with fresh UUID v4 values."""
    product.id = str(uuid.uuid4())
    product.reference = str(uuid.uuid4())
    for option in product.options:
        option.id = str(uuid.uuid4())
        option.reference = str(uuid.uuid4())
        for unit in option.units:
            unit.id = str(uuid.uuid4())
            unit.reference = str(uuid.uuid4())
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

    async def generate_products(self, count: int) -> list[Product]:
        """Generate `count` Products via Ollama with retry + validation.

        Raises SeedingFailedError if all retries exhausted for any product.
        """
        gen_start = time.monotonic()
        products: list[Product] = []
        for i in range(count):
            logger.info("Generating product %d/%d", i + 1, count)
            product = await self._generate_single_product(product_num=i + 1)
            products.append(product)

        gen_duration = time.monotonic() - gen_start
        if self._tel:
            self._tel.seeder_generation_duration_seconds.record(gen_duration)

        logger.info("Generated %d products", count)
        return products

    async def _generate_single_product(self, product_num: int) -> Product:
        """Generate one product with retry logic.

        Raises SeedingFailedError if all retries exhausted.
        """
        error_hints: list[str] = []
        product_start = time.monotonic()

        for attempt in range(1, self._max_retries + 1):
            prompt = self._prompt_builder.build_prompt(
                error_hints=error_hints if error_hints else None
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
