"""Orchestrates Product generation via Ollama with retry and validation.

Calls the LLM, parses JSON output, validates against the Product model,
assigns fresh UUIDs, and checks against the RealSamplesIndex to ensure
zero production data leakage.
"""

import asyncio
import json
import logging
import re
import time
import uuid
from typing import Any

from enum import Enum

from pydantic import BaseModel

from models.product import Product
from seeder.ollama_client import (
    OllamaClient,
    OllamaInvalidResponseError,
    OllamaUnreachableError,
    SeedingFailedError,
)
from seeder.prompt_builder import PromptBuilder
from seeder.sample_index import RealSamplesIndex
from telemetry.setup import TelemetryInstruments

logger = logging.getLogger(__name__)

# Regex to strip markdown code fences from LLM output
_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?\s*```$", re.DOTALL)


def _strip_code_fences(text: str) -> str:
    """Remove markdown code fences if present."""
    text = text.strip()
    m = _CODE_FENCE_RE.match(text)
    return m.group(1).strip() if m else text


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


def _extract_string_fields(obj: Any) -> list[tuple[str, str]]:
    """Recursively extract all (field_path, string_value) pairs from a Pydantic model.

    Enum values are skipped because they come from the OCTO spec (fixed vocabulary)
    and will always match real samples — they cannot constitute production data leakage.
    """
    results: list[tuple[str, str]] = []

    if isinstance(obj, Enum):
        return []

    if isinstance(obj, str):
        return [("", obj)]

    if isinstance(obj, BaseModel):
        # Pydantic model — access model_fields from the class
        for field_name in type(obj).model_fields:
            value = getattr(obj, field_name)
            for sub_path, sub_val in _extract_string_fields(value):
                path = f"{field_name}.{sub_path}" if sub_path else field_name
                results.append((path, sub_val))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            for sub_path, sub_val in _extract_string_fields(item):
                path = f"[{i}].{sub_path}" if sub_path else f"[{i}]"
                results.append((path, sub_val))
    elif isinstance(obj, dict):
        for key, value in obj.items():
            for sub_path, sub_val in _extract_string_fields(value):
                path = f"{key}.{sub_path}" if sub_path else key
                results.append((path, sub_val))

    return results


class ProductGenerator:
    """Generates synthetic OCTO Products via Ollama with retry and validation."""

    def __init__(
        self,
        ollama_client: OllamaClient,
        prompt_builder: PromptBuilder,
        sample_index: RealSamplesIndex,
        max_retries: int = 3,
        telemetry: TelemetryInstruments | None = None,
    ) -> None:
        self._client = ollama_client
        self._prompt_builder = prompt_builder
        self._index = sample_index
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

        logger.info(
            "Generated %d products, 0 production data matches found", count
        )
        return products

    async def _generate_single_product(self, product_num: int) -> Product:
        """Generate one product with retry logic.

        Raises SeedingFailedError if all retries exhausted.
        """
        prompt = self._prompt_builder.build_prompt()
        product_start = time.monotonic()

        for attempt in range(1, self._max_retries + 1):
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
                    raise OllamaInvalidResponseError(
                        f"Failed to parse LLM output as JSON: {exc}"
                    ) from exc

                # Validate against Product model
                try:
                    product = Product.model_validate(data)
                except Exception as exc:
                    raise OllamaInvalidResponseError(
                        f"LLM output does not conform to Product schema: {exc}"
                    ) from exc

                # Assign fresh UUIDs
                product = _assign_fresh_uuids(product)

                # Check against RealSamplesIndex
                match_found = False
                for field_path, value in _extract_string_fields(product):
                    if self._index.check(value):
                        logger.warning(
                            "Product %d: production data match on field '%s' = '%s', discarding",
                            product_num, field_path, value,
                        )
                        if self._tel:
                            self._tel.seeder_validation_failures_total.add(1)
                            self._tel.seeder_errors_total.add(
                                1, {"error_type": "VALIDATION_FAILURE"}
                            )
                        match_found = True
                        break

                if match_found:
                    # Counts as a retry — re-generate
                    if attempt < self._max_retries and self._tel:
                        self._tel.seeder_ollama_retries_total.add(1)
                    continue

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
