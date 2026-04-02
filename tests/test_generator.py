"""Tests for ProductGenerator — retry logic, validation pipeline, and UUID assignment."""

import json
import uuid

import pytest

from models.product import Product
from seeder.generator import (
    ProductGenerator,
    _assign_fresh_uuids,
    _normalize_llm_output,
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
        "reference": None,
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
                "reference": None,
                "availabilityLocalStartTimes": ["09:00", "14:00"],
                "cancellationCutoff": "24 hours",
                "cancellationCutoffAmount": 24,
                "cancellationCutoffUnit": "hour",
                "requiredContactFields": [],
                "restrictions": {"minUnits": 0, "maxUnits": None},
                "units": [
                    {
                        "id": "old-unit-id",
                        "internalName": "Adult Ticket",
                        "reference": None,
                        "type": "ADULT",
                        "requiredContactFields": [],
                        "restrictions": {
                            "minAge": 18,
                            "maxAge": 64,
                            "idRequired": False,
                            "minQuantity": 1,
                            "maxQuantity": None,
                            "paxCount": 1,
                            "accompaniedBy": [],
                        },
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

    def __init__(self) -> None:
        self.last_previously_generated: list[dict] | None = None

    def build_prompt(
        self,
        error_hints: list[str] | None = None,
        previously_generated: list[dict] | None = None,
    ) -> str:
        self.last_previously_generated = previously_generated
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
        # All IDs should be valid UUID v4
        uuid.UUID(result.id, version=4)
        uuid.UUID(result.options[0].id, version=4)
        uuid.UUID(result.options[0].units[0].id, version=4)
        # References should be None (not UUIDs)
        assert result.reference is None
        assert result.options[0].reference is None
        assert result.options[0].units[0].reference is None


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


# ---------------------------------------------------------------------------
# Unit tests: _normalize_llm_output
# ---------------------------------------------------------------------------

class TestNormalizeLlmOutput:
    """Tests for the LLM output normalizer that backfills omitted defaults."""

    def test_backfills_unit_required_contact_fields(self):
        data = _make_valid_product_dict()
        del data["options"][0]["units"][0]["requiredContactFields"]
        result = _normalize_llm_output(data)
        assert result["options"][0]["units"][0]["requiredContactFields"] == []

    def test_backfills_unit_restrictions_entirely_missing(self):
        data = _make_valid_product_dict()
        del data["options"][0]["units"][0]["restrictions"]
        result = _normalize_llm_output(data)
        r = result["options"][0]["units"][0]["restrictions"]
        assert r == {
            "minAge": 0,
            "maxAge": 0,
            "idRequired": False,
            "minQuantity": None,
            "maxQuantity": None,
            "paxCount": 1,
            "accompaniedBy": [],
        }

    def test_backfills_individual_restriction_fields(self):
        data = _make_valid_product_dict()
        # Keep only minAge and maxAge, omit the rest
        data["options"][0]["units"][0]["restrictions"] = {
            "minAge": 18,
            "maxAge": 65,
        }
        result = _normalize_llm_output(data)
        r = result["options"][0]["units"][0]["restrictions"]
        assert r["idRequired"] is False
        assert r["minQuantity"] is None
        assert r["maxQuantity"] is None
        assert r["paxCount"] == 1
        assert r["accompaniedBy"] == []
        # Existing values preserved
        assert r["minAge"] == 18
        assert r["maxAge"] == 65

    def test_backfills_option_required_contact_fields(self):
        data = _make_valid_product_dict()
        del data["options"][0]["requiredContactFields"]
        result = _normalize_llm_output(data)
        assert result["options"][0]["requiredContactFields"] == []

    def test_backfills_option_restrictions_missing(self):
        data = _make_valid_product_dict()
        del data["options"][0]["restrictions"]
        result = _normalize_llm_output(data)
        r = result["options"][0]["restrictions"]
        assert r == {"minUnits": 0, "maxUnits": None}

    def test_backfills_option_restrictions_partial(self):
        data = _make_valid_product_dict()
        data["options"][0]["restrictions"] = {"minUnits": 2}
        result = _normalize_llm_output(data)
        r = result["options"][0]["restrictions"]
        assert r["minUnits"] == 2
        assert r["maxUnits"] is None

    def test_derives_cutoff_amount_and_unit(self):
        data = _make_valid_product_dict()
        del data["options"][0]["cancellationCutoffAmount"]
        del data["options"][0]["cancellationCutoffUnit"]
        data["options"][0]["cancellationCutoff"] = "48 hours"
        result = _normalize_llm_output(data)
        assert result["options"][0]["cancellationCutoffAmount"] == 48
        assert result["options"][0]["cancellationCutoffUnit"] == "hour"

    def test_coerces_duration_amount_int_to_str(self):
        data = _make_valid_product_dict()
        data["options"][0]["durationAmount"] = 3
        result = _normalize_llm_output(data)
        assert result["options"][0]["durationAmount"] == "3"

    def test_preserves_existing_values(self):
        """Normalizer should not overwrite values the LLM did provide."""
        data = _make_valid_product_dict()
        data["options"][0]["units"][0]["requiredContactFields"] = ["emailAddress"]
        data["options"][0]["units"][0]["restrictions"]["paxCount"] = 4
        result = _normalize_llm_output(data)
        assert result["options"][0]["units"][0]["requiredContactFields"] == ["emailAddress"]
        assert result["options"][0]["units"][0]["restrictions"]["paxCount"] == 4

    def test_normalized_output_validates_as_product(self):
        """A minimal LLM output with many fields omitted should validate
        after normalization."""
        minimal = {
            "id": "test-id",
            "internalName": "City Walking Tour",
            "reference": None,
            "locale": "en",
            "timeZone": "Europe/London",
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
                    "id": "opt-1",
                    "default": False,
                    "internalName": "Standard",
                    "reference": None,
                    "availabilityLocalStartTimes": ["10:00"],
                    "cancellationCutoff": "24 hours",
                    # omitted: cancellationCutoffAmount, cancellationCutoffUnit
                    # omitted: requiredContactFields, restrictions
                    "units": [
                        {
                            "id": "unit-1",
                            "internalName": "Adult",
                            "reference": None,
                            "type": "ADULT",
                            # omitted: requiredContactFields, restrictions
                        }
                    ],
                }
            ],
        }
        normalized = _normalize_llm_output(minimal)
        product = Product.model_validate(normalized)
        assert product.options[0].cancellation_cutoff_amount == 24
        assert product.options[0].units[0].restrictions.pax_count == 1


    def test_backfills_option_default(self):
        data = _make_valid_product_dict()
        del data["options"][0]["default"]
        result = _normalize_llm_output(data)
        assert result["options"][0]["default"] is False

    def test_backfills_option_availability_local_start_times_opening_hours(self):
        data = _make_valid_product_dict()
        data["availabilityType"] = "OPENING_HOURS"
        del data["options"][0]["availabilityLocalStartTimes"]
        result = _normalize_llm_output(data)
        assert result["options"][0]["availabilityLocalStartTimes"] == []

    def test_backfills_option_availability_local_start_times_start_time(self):
        data = _make_valid_product_dict()
        data["availabilityType"] = "START_TIME"
        del data["options"][0]["availabilityLocalStartTimes"]
        result = _normalize_llm_output(data)
        times = result["options"][0]["availabilityLocalStartTimes"]
        assert isinstance(times, list)
        assert len(times) >= 1  # picked from fallback pool

    def test_backfills_option_cancellation_cutoff(self):
        data = _make_valid_product_dict()
        del data["options"][0]["cancellationCutoff"]
        del data["options"][0]["cancellationCutoffAmount"]
        del data["options"][0]["cancellationCutoffUnit"]
        result = _normalize_llm_output(data)
        assert result["options"][0]["cancellationCutoff"] == "0 hours"
        assert result["options"][0]["cancellationCutoffAmount"] == 0
        assert result["options"][0]["cancellationCutoffUnit"] == "hour"

    def test_backfills_option_internal_name_from_title(self):
        data = _make_valid_product_dict()
        del data["options"][0]["internalName"]
        data["options"][0]["title"] = "Premium Tour"
        result = _normalize_llm_output(data)
        assert result["options"][0]["internalName"] == "Premium Tour"

    def test_backfills_product_internal_name_from_title(self):
        data = _make_valid_product_dict()
        del data["internalName"]
        data["title"] = "Harbor Cruise"
        result = _normalize_llm_output(data)
        assert result["internalName"] == "Harbor Cruise"

    def test_backfills_product_internal_name_from_pool(self):
        """When both internalName and title are missing, picks from pool."""
        data = _make_valid_product_dict()
        del data["internalName"]
        result = _normalize_llm_output(data)
        from seeder.generator import _FALLBACK_PRODUCT_NAMES
        assert result["internalName"] in _FALLBACK_PRODUCT_NAMES

    def test_backfills_option_internal_name_from_pool(self):
        """When both internalName and title are missing on option, picks from pool."""
        data = _make_valid_product_dict()
        del data["options"][0]["internalName"]
        result = _normalize_llm_output(data)
        from seeder.generator import _FALLBACK_OPTION_NAMES
        assert result["options"][0]["internalName"] in _FALLBACK_OPTION_NAMES


# ---------------------------------------------------------------------------
# Unit tests: diversity — previously_generated passed through pipeline
# ---------------------------------------------------------------------------

class TestDiversityPipeline:
    @pytest.mark.anyio
    async def test_first_product_gets_no_previously_generated(self):
        """First product in a batch should not receive previously_generated."""
        product_dict = _make_valid_product_dict()
        client = FakeOllamaClient(responses=[_ollama_response(product_dict)])
        prompt_builder = FakePromptBuilder()
        gen = ProductGenerator(client, prompt_builder, max_retries=3)

        await gen.generate_products(1)

        assert prompt_builder.last_previously_generated is None

    @pytest.mark.anyio
    async def test_second_product_gets_first_summary(self):
        """Second product should receive a summary of the first."""
        product_dict = _make_valid_product_dict()
        client = FakeOllamaClient(
            responses=[_ollama_response(product_dict)] * 2
        )
        prompt_builder = FakePromptBuilder()
        gen = ProductGenerator(client, prompt_builder, max_retries=3)

        await gen.generate_products(2)

        prev = prompt_builder.last_previously_generated
        assert prev is not None
        assert len(prev) == 1
        assert "title" in prev[0]
        assert "country" in prev[0]
        assert "availabilityType" in prev[0]
        assert "categoryLabels" in prev[0]

    @pytest.mark.anyio
    async def test_nth_product_gets_all_previous_summaries(self):
        """The Nth product should receive N-1 summaries."""
        product_dict = _make_valid_product_dict()
        client = FakeOllamaClient(
            responses=[_ollama_response(product_dict)] * 5
        )
        prompt_builder = FakePromptBuilder()
        gen = ProductGenerator(client, prompt_builder, max_retries=3)

        await gen.generate_products(5)

        # After generating 5, the last call should have received 4 summaries
        prev = prompt_builder.last_previously_generated
        assert prev is not None
        assert len(prev) == 4

    @pytest.mark.anyio
    async def test_product_summary_fields(self):
        """Verify the summary dict has the expected shape."""
        product_dict = _make_valid_product_dict(
            title="City Walking Tour",
            country="GB",
            availabilityType="OPENING_HOURS",
            categoryLabels=["WALKING_TOUR", "CULTURAL"],
        )
        client = FakeOllamaClient(
            responses=[_ollama_response(product_dict)] * 2
        )
        prompt_builder = FakePromptBuilder()
        gen = ProductGenerator(client, prompt_builder, max_retries=3)

        await gen.generate_products(2)

        prev = prompt_builder.last_previously_generated
        assert prev is not None
        summary = prev[0]
        assert summary["title"] == "City Walking Tour"
        assert summary["country"] == "GB"
        assert summary["availabilityType"] == "OPENING_HOURS"
        assert summary["categoryLabels"] == ["WALKING_TOUR", "CULTURAL"]

    @pytest.mark.anyio
    async def test_summary_uses_internal_name_when_title_missing(self):
        """When title is None, summary should fall back to internalName."""
        product_dict = _make_valid_product_dict()
        # Ensure no title field so it falls back to internalName
        product_dict.pop("title", None)
        client = FakeOllamaClient(
            responses=[_ollama_response(product_dict)] * 2
        )
        prompt_builder = FakePromptBuilder()
        gen = ProductGenerator(client, prompt_builder, max_retries=3)

        await gen.generate_products(2)

        prev = prompt_builder.last_previously_generated
        assert prev[0]["title"] == "Fictional Tour"  # internalName from helper
