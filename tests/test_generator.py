"""Tests for ProductGenerator — retry logic, validation pipeline, and UUID assignment."""

import json
import uuid

import pytest

from models.product import Product
from seeder.generator import (
    ProductGenerator,
    _assign_fresh_uuids,
    _strip_code_fences,
)
from seeder.ollama_client import (
    OllamaInvalidResponseError,
    OllamaResponse,
    OllamaUnreachableError,
    SeedingFailedError,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_valid_product_dict(**overrides) -> dict:
    """Return a minimal valid Product dict for LLM output simulation."""
    base = {
        "id": "old-id",
        "internalName": "Fictional Tour",
        "reference": "old-ref",
        "locale": "en",
        "timeZone": "Europe/Berlin",
        "allowFreesale": False,
        "instantConfirmation": True,
        "instantDelivery": True,
        "availabilityRequired": True,
        "availabilityType": "START_TIME",
        "deliveryFormats": ["QRCODE"],
        "deliveryMethods": ["VOUCHER"],
        "redemptionMethod": "DIGITAL",
        "options": [
            {
                "id": "old-opt-id",
                "default": True,
                "internalName": "Standard Pass",
                "reference": "old-opt-ref",
                "availabilityLocalStartTimes": ["09:00", "14:00"],
                "cancellationCutoff": "24 hours",
                "cancellationCutoffAmount": 24,
                "cancellationCutoffUnit": "hour",
                "units": [
                    {
                        "id": "old-unit-id",
                        "internalName": "Adult Ticket",
                        "reference": "old-unit-ref",
                        "type": "ADULT",
                    }
                ],
            }
        ],
    }
    base.update(overrides)
    return base


def _ollama_response(product_dict: dict) -> OllamaResponse:
    """Wrap a product dict into an OllamaResponse."""
    return OllamaResponse(
        response=json.dumps(product_dict),
        total_duration=1000,
        eval_duration=800,
        eval_count=50,
        prompt_eval_count=20,
    )


class FakePromptBuilder:
    """Stub that returns a fixed prompt."""

    def build_prompt(self, error_hints: list[str] | None = None) -> str:
        return "Generate a product"


class FakeOllamaClient:
    """Configurable fake OllamaClient for testing."""

    def __init__(self, responses: list | None = None, errors: list | None = None) -> None:
        self._responses = list(responses or [])
        self._errors = list(errors or [])
        self._call_count = 0

    async def generate(self, prompt: str) -> OllamaResponse:
        idx = self._call_count
        self._call_count += 1
        if idx < len(self._errors) and self._errors[idx] is not None:
            raise self._errors[idx]
        if idx < len(self._responses):
            return self._responses[idx]
        raise OllamaInvalidResponseError("No more responses configured")


# ---------------------------------------------------------------------------
# Unit tests: helpers
# ---------------------------------------------------------------------------

class TestStripCodeFences:
    def test_plain_json(self):
        raw = '{"id": "abc"}'
        assert _strip_code_fences(raw) == '{"id": "abc"}'

    def test_json_code_fence(self):
        raw = '```json\n{"id": "abc"}\n```'
        assert _strip_code_fences(raw) == '{"id": "abc"}'

    def test_bare_code_fence(self):
        raw = '```\n{"id": "abc"}\n```'
        assert _strip_code_fences(raw) == '{"id": "abc"}'

    def test_whitespace_around_fences(self):
        raw = '  ```json\n{"id": "abc"}\n```  '
        assert _strip_code_fences(raw) == '{"id": "abc"}'


class TestAssignFreshUuids:
    def test_all_ids_replaced(self):
        product = Product.model_validate(_make_valid_product_dict())
        old_id = product.id
        old_opt_id = product.options[0].id
        old_unit_id = product.options[0].units[0].id

        result = _assign_fresh_uuids(product)

        assert result.id != old_id
        assert result.options[0].id != old_opt_id
        assert result.options[0].units[0].id != old_unit_id
        # All should be valid UUID v4
        uuid.UUID(result.id, version=4)
        uuid.UUID(result.reference, version=4)
        uuid.UUID(result.options[0].id, version=4)
        uuid.UUID(result.options[0].units[0].id, version=4)


# ---------------------------------------------------------------------------
# Unit tests: ProductGenerator
# ---------------------------------------------------------------------------

class TestGenerateProductsSuccess:
    @pytest.mark.anyio
    async def test_generates_requested_count(self):
        product_dict = _make_valid_product_dict()
        client = FakeOllamaClient(
            responses=[_ollama_response(product_dict)] * 3
        )
        gen = ProductGenerator(client, FakePromptBuilder(), max_retries=3)

        products = await gen.generate_products(2)

        assert len(products) == 2
        for p in products:
            assert isinstance(p, Product)

    @pytest.mark.anyio
    async def test_uuids_are_fresh(self):
        product_dict = _make_valid_product_dict()
        client = FakeOllamaClient(responses=[_ollama_response(product_dict)])
        gen = ProductGenerator(client, FakePromptBuilder(), max_retries=3)

        products = await gen.generate_products(1)
        p = products[0]

        assert p.id != "old-id"
        assert p.options[0].id != "old-opt-id"
        assert p.options[0].units[0].id != "old-unit-id"
        uuid.UUID(p.id, version=4)


class TestRetryOnInvalidResponse:
    @pytest.mark.anyio
    async def test_retries_on_invalid_json(self):
        """First call returns garbage, second returns valid product."""
        valid = _ollama_response(_make_valid_product_dict())
        client = FakeOllamaClient(
            responses=[
                OllamaResponse(
                    response="not json at all",
                    total_duration=100, eval_duration=80,
                    eval_count=10, prompt_eval_count=5,
                ),
                valid,
            ]
        )
        gen = ProductGenerator(client, FakePromptBuilder(), max_retries=3)

        products = await gen.generate_products(1)
        assert len(products) == 1

    @pytest.mark.anyio
    async def test_retries_on_schema_violation(self):
        """First call returns JSON that doesn't match Product schema, second is valid."""
        bad_schema = OllamaResponse(
            response='{"not_a_product": true}',
            total_duration=100, eval_duration=80,
            eval_count=10, prompt_eval_count=5,
        )
        valid = _ollama_response(_make_valid_product_dict())
        client = FakeOllamaClient(responses=[bad_schema, valid])
        gen = ProductGenerator(client, FakePromptBuilder(), max_retries=3)

        products = await gen.generate_products(1)
        assert len(products) == 1


class TestRetryOnUnreachable:
    @pytest.mark.anyio
    async def test_retries_with_backoff_on_unreachable(self, monkeypatch):
        """Unreachable on first attempt, succeeds on second."""
        valid = _ollama_response(_make_valid_product_dict())
        client = FakeOllamaClient(
            responses=[None, valid],
            errors=[OllamaUnreachableError("down"), None],
        )

        sleep_calls = []
        async def fake_sleep(seconds):
            sleep_calls.append(seconds)

        monkeypatch.setattr("seeder.generator.asyncio.sleep", fake_sleep)

        gen = ProductGenerator(client, FakePromptBuilder(), max_retries=3)
        products = await gen.generate_products(1)

        assert len(products) == 1
        assert sleep_calls == [2]


class TestExhaustedRetries:
    @pytest.mark.anyio
    async def test_raises_seeding_failed_error(self):
        """All retries fail → SeedingFailedError."""
        client = FakeOllamaClient(
            errors=[
                OllamaInvalidResponseError("bad1"),
                OllamaInvalidResponseError("bad2"),
                OllamaInvalidResponseError("bad3"),
            ]
        )
        gen = ProductGenerator(client, FakePromptBuilder(), max_retries=3)

        with pytest.raises(SeedingFailedError, match="after 3 attempts"):
            await gen.generate_products(1)


class TestCodeFenceStripping:
    @pytest.mark.anyio
    async def test_handles_markdown_wrapped_json(self):
        """LLM wraps JSON in code fences — generator should still parse it."""
        product_dict = _make_valid_product_dict()
        wrapped = f"```json\n{json.dumps(product_dict)}\n```"
        client = FakeOllamaClient(
            responses=[
                OllamaResponse(
                    response=wrapped,
                    total_duration=100, eval_duration=80,
                    eval_count=10, prompt_eval_count=5,
                )
            ]
        )
        gen = ProductGenerator(client, FakePromptBuilder(), max_retries=3)

        products = await gen.generate_products(1)
        assert len(products) == 1
