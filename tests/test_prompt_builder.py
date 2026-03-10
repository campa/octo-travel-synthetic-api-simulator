"""Tests for the PromptBuilder class."""

import json
import os
import tempfile

import pytest

from seeder.prompt_builder import PromptBuilder


@pytest.fixture
def sample_dir(tmp_path):
    """Create a temp directory with fake product sample files."""
    subdir = tmp_path / "prodcuts-and-availability-calendar-responses"
    subdir.mkdir()

    # Create two sample product files
    for supplier_id in ("aaaa", "bbbb"):
        products = [
            {
                "id": f"{supplier_id}-prod-id-1234",
                "internalName": f"Sample Tour {supplier_id}",
                "reference": f"REF-{supplier_id}",
                "locale": "en",
                "timeZone": "Europe/London",
                "allowFreesale": False,
                "instantConfirmation": True,
                "instantDelivery": True,
                "availabilityRequired": True,
                "availabilityType": "START_TIME",
                "deliveryFormats": ["PDF_URL"],
                "deliveryMethods": ["VOUCHER"],
                "redemptionMethod": "DIGITAL",
                "options": [
                    {
                        "id": f"{supplier_id}-opt-id",
                        "default": True,
                        "internalName": "Standard",
                        "reference": None,
                        "availabilityLocalStartTimes": ["09:00", "14:00"],
                        "cancellationCutoff": "24 hours",
                        "cancellationCutoffAmount": 24,
                        "cancellationCutoffUnit": "hour",
                        "requiredContactFields": [],
                        "restrictions": {"minUnits": 1, "maxUnits": 10},
                        "units": [
                            {
                                "id": f"{supplier_id}-unit-adult",
                                "internalName": "Adult",
                                "reference": None,
                                "type": "ADULT",
                                "requiredContactFields": [],
                                "restrictions": {
                                    "minAge": 18,
                                    "maxAge": 64,
                                    "idRequired": False,
                                    "minQuantity": 1,
                                    "maxQuantity": 10,
                                    "paxCount": 1,
                                    "accompaniedBy": [],
                                },
                            }
                        ],
                    }
                ],
                # Extra fields that should be trimmed
                "title": "Verbose Title",
                "description": "<p>Long HTML</p>",
                "galleryImages": [{"url": "http://example.com/img.jpg"}],
                "faqs": [{"question": "Q?", "answer": "A."}],
            }
        ]
        filepath = subdir / f"{supplier_id}-products.json"
        filepath.write_text(json.dumps(products), encoding="utf-8")

    return str(tmp_path)


class TestPromptBuilder:
    def test_loads_examples_from_sample_dir(self, sample_dir):
        builder = PromptBuilder(samples_dir=sample_dir, avg_slots_per_day=3)
        assert len(builder._examples) == 2

    def test_build_prompt_contains_schema(self, sample_dir):
        builder = PromptBuilder(samples_dir=sample_dir)
        prompt = builder.build_prompt()
        assert "OCTO Product JSON Schema" in prompt
        assert '"internalName"' in prompt
        assert '"availabilityType"' in prompt

    def test_build_prompt_contains_few_shot_examples(self, sample_dir):
        builder = PromptBuilder(samples_dir=sample_dir)
        prompt = builder.build_prompt()
        assert "Example 1" in prompt
        assert "Example 2" in prompt

    def test_build_prompt_contains_fictional_data_instruction(self, sample_dir):
        builder = PromptBuilder(samples_dir=sample_dir)
        prompt = builder.build_prompt()
        assert "NEVER reproduce" in prompt
        assert "entirely fictional" in prompt

    def test_examples_are_trimmed(self, sample_dir):
        """Verbose fields like galleryImages, faqs, description should be stripped."""
        builder = PromptBuilder(samples_dir=sample_dir)
        prompt = builder.build_prompt()
        assert "galleryImages" not in prompt
        assert "faqs" not in prompt
        assert "Long HTML" not in prompt
        assert "Verbose Title" not in prompt

    def test_examples_keep_essential_fields(self, sample_dir):
        builder = PromptBuilder(samples_dir=sample_dir)
        prompt = builder.build_prompt()
        # Essential product fields should be present in examples
        assert "availabilityType" in prompt
        assert "deliveryFormats" in prompt
        assert "redemptionMethod" in prompt

    def test_avg_slots_per_day_in_prompt(self, sample_dir):
        builder = PromptBuilder(samples_dir=sample_dir, avg_slots_per_day=5)
        prompt = builder.build_prompt()
        assert "approximately 5 entries" in prompt

    def test_no_samples_dir_still_builds_prompt(self, tmp_path):
        """Prompt should still work even with no sample files."""
        builder = PromptBuilder(samples_dir=str(tmp_path / "nonexistent"))
        prompt = builder.build_prompt()
        assert "OCTO Product JSON Schema" in prompt
        assert "Generation Instructions" in prompt

    def test_max_three_examples(self, tmp_path):
        """Should load at most 3 examples even if more files exist."""
        subdir = tmp_path / "prodcuts-and-availability-calendar-responses"
        subdir.mkdir()
        for i in range(5):
            products = [{"id": f"id-{i}", "internalName": f"Tour {i}"}]
            filepath = subdir / f"{i:04d}-products.json"
            filepath.write_text(json.dumps(products), encoding="utf-8")

        builder = PromptBuilder(samples_dir=str(tmp_path))
        assert len(builder._examples) == 3

    def test_skips_invalid_json_files(self, tmp_path):
        """Should gracefully skip files with invalid JSON."""
        subdir = tmp_path / "prodcuts-and-availability-calendar-responses"
        subdir.mkdir()
        # Valid file
        valid = [{"id": "valid-id", "internalName": "Valid Tour"}]
        (subdir / "aaaa-products.json").write_text(json.dumps(valid))
        # Invalid file
        (subdir / "bbbb-products.json").write_text("not json{{{")

        builder = PromptBuilder(samples_dir=str(tmp_path))
        assert len(builder._examples) == 1

    def test_build_prompt_requests_single_product(self, sample_dir):
        builder = PromptBuilder(samples_dir=sample_dir)
        prompt = builder.build_prompt()
        assert "exactly ONE" in prompt

    def test_build_prompt_instructs_raw_json_output(self, sample_dir):
        builder = PromptBuilder(samples_dir=sample_dir)
        prompt = builder.build_prompt()
        assert "ONLY the raw JSON" in prompt
