"""Tests for ProductGenerator — retry logic, validation pipeline, UUID assignment, and data isolation."""

import json
import uuid

import pytest

from models.product import (
    AvailabilityType,
    DeliveryFormat,
    DeliveryMethod,
    Option,
    Product,
    RedemptionMethod,
    Unit,
    UnitType,
)
from seeder.generator import (
    ProductGenerator,
    _assign_fresh_uuids,
    _extract_string_fields,
    _strip_code_fences,
)
from seeder.ollama_client import (
    OllamaInvalidResponseError,
    OllamaResponse,
    OllamaUnreachableError,
    SeedingFailedError,
)
from seeder.prompt_builder import PromptBuilder
from seeder.sample_index import RealSamplesIndex


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

    def build_prompt(self) -> str:
        return "Generate a product"


class FakeIndex:
    """Stub RealSamplesIndex that never matches."""

    def check(self, value: str) -> bool:
        return False


class MatchingIndex:
    """Stub RealSamplesIndex that matches a specific value."""

    def __init__(self, match_value: str) -> None:
        self._match = match_value

    def check(self, value: str) -> bool:
        if len(value) < 4:
            return False
        return value == self._match


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


class TestExtractStringFields:
    def test_extracts_product_strings(self):
        product = Product.model_validate(_make_valid_product_dict())
        fields = _extract_string_fields(product)
        values = [v for _, v in fields]
        assert "Fictional Tour" in values
        assert "Standard Pass" in values
        assert "Adult Ticket" in values

    def test_includes_nested_option_and_unit_fields(self):
        product = Product.model_validate(_make_valid_product_dict())
        fields = _extract_string_fields(product)
        paths = [p for p, _ in fields]
        assert any("options" in p for p in paths)


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
        gen = ProductGenerator(client, FakePromptBuilder(), FakeIndex(), max_retries=3)

        products = await gen.generate_products(2)

        assert len(products) == 2
        for p in products:
            assert isinstance(p, Product)

    @pytest.mark.anyio
    async def test_uuids_are_fresh(self):
        product_dict = _make_valid_product_dict()
        client = FakeOllamaClient(responses=[_ollama_response(product_dict)])
        gen = ProductGenerator(client, FakePromptBuilder(), FakeIndex(), max_retries=3)

        products = await gen.generate_products(1)
        p = products[0]

        # IDs should not be the old ones from the dict
        assert p.id != "old-id"
        assert p.options[0].id != "old-opt-id"
        assert p.options[0].units[0].id != "old-unit-id"
        # Should be valid UUIDs
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
        gen = ProductGenerator(client, FakePromptBuilder(), FakeIndex(), max_retries=3)

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
        gen = ProductGenerator(client, FakePromptBuilder(), FakeIndex(), max_retries=3)

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

        # Patch asyncio.sleep to avoid real delays
        sleep_calls = []
        async def fake_sleep(seconds):
            sleep_calls.append(seconds)

        monkeypatch.setattr("seeder.generator.asyncio.sleep", fake_sleep)

        gen = ProductGenerator(client, FakePromptBuilder(), FakeIndex(), max_retries=3)
        products = await gen.generate_products(1)

        assert len(products) == 1
        # Exponential backoff: 2^1 = 2 seconds
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
        gen = ProductGenerator(client, FakePromptBuilder(), FakeIndex(), max_retries=3)

        with pytest.raises(SeedingFailedError, match="after 3 attempts"):
            await gen.generate_products(1)


class TestProductionDataIsolation:
    @pytest.mark.anyio
    async def test_rejects_product_matching_index(self):
        """Product with a field matching the index is discarded, next attempt succeeds."""
        matching_dict = _make_valid_product_dict(internalName="RealTourName")
        clean_dict = _make_valid_product_dict(internalName="FictionalTour")

        client = FakeOllamaClient(
            responses=[
                _ollama_response(matching_dict),
                _ollama_response(clean_dict),
            ]
        )
        index = MatchingIndex("RealTourName")
        gen = ProductGenerator(client, FakePromptBuilder(), index, max_retries=3)

        products = await gen.generate_products(1)
        assert len(products) == 1
        assert products[0].internal_name == "FictionalTour"

    @pytest.mark.anyio
    async def test_short_strings_not_checked(self):
        """Strings shorter than 4 chars should not trigger rejection."""
        product_dict = _make_valid_product_dict(locale="en")
        client = FakeOllamaClient(responses=[_ollama_response(product_dict)])
        # Index that would match "en" if length check wasn't in place
        index = MatchingIndex("en")
        gen = ProductGenerator(client, FakePromptBuilder(), index, max_retries=3)

        products = await gen.generate_products(1)
        assert len(products) == 1


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
        gen = ProductGenerator(client, FakePromptBuilder(), FakeIndex(), max_retries=3)

        products = await gen.generate_products(1)
        assert len(products) == 1
