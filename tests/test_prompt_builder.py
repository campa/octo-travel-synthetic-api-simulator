"""Tests for the PromptBuilder class."""

import pytest

from seeder.prompt_builder import PromptBuilder


class TestPromptBuilder:
    def test_build_prompt_contains_schema(self):
        builder = PromptBuilder()
        prompt = builder.build_prompt()
        assert "OCTO Product JSON Schema" in prompt

    def test_build_prompt_contains_generation_instructions(self):
        builder = PromptBuilder()
        prompt = builder.build_prompt()
        assert "Generation Instructions" in prompt

    def test_build_prompt_requests_single_product(self):
        builder = PromptBuilder()
        prompt = builder.build_prompt()
        assert "exactly ONE" in prompt

    def test_build_prompt_instructs_raw_json_output(self):
        builder = PromptBuilder()
        prompt = builder.build_prompt()
        assert "ONLY the raw JSON" in prompt

    def test_avg_slots_per_day_in_prompt(self):
        builder = PromptBuilder(avg_slots_per_day=5)
        prompt = builder.build_prompt()
        # The prompt should mention start time variety
        assert "START_TIME" in prompt

    def test_build_prompt_instructs_fictional_data(self):
        builder = PromptBuilder()
        prompt = builder.build_prompt()
        assert "fictional" in prompt

    def test_build_prompt_without_error_hints(self):
        builder = PromptBuilder()
        prompt = builder.build_prompt()
        assert "Previous Attempt Errors" not in prompt

    def test_build_prompt_with_error_hints(self):
        builder = PromptBuilder()
        prompt = builder.build_prompt(error_hints=[
            "Field 'paxCount' must be greater than 0",
            "Field 'minAge' must be >= 0",
        ])
        assert "Previous Attempt Errors" in prompt
        assert "paxCount" in prompt
        assert "minAge" in prompt

    def test_error_hints_numbered(self):
        builder = PromptBuilder()
        prompt = builder.build_prompt(error_hints=["error one", "error two"])
        assert "1. error one" in prompt
        assert "2. error two" in prompt


    def test_build_prompt_without_previously_generated(self):
        builder = PromptBuilder()
        prompt = builder.build_prompt()
        assert "### Diversity" not in prompt

    def test_build_prompt_with_previously_generated(self):
        builder = PromptBuilder()
        previously = [
            {"title": "Sunset River Cruise", "country": "US",
             "availabilityType": "START_TIME", "categoryLabels": ["CRUISE"]},
        ]
        prompt = builder.build_prompt(previously_generated=previously)
        assert "### Diversity" in prompt
        assert "27." in prompt
        assert "Sunset River Cruise" in prompt

    def test_previously_generated_numbered(self):
        builder = PromptBuilder()
        previously = [
            {"title": "Product A", "country": "US",
             "availabilityType": "START_TIME", "categoryLabels": []},
            {"title": "Product B", "country": "DE",
             "availabilityType": "OPENING_HOURS", "categoryLabels": []},
        ]
        prompt = builder.build_prompt(previously_generated=previously)
        assert "1." in prompt
        assert "2." in prompt
        assert "Product A" in prompt
        assert "Product B" in prompt

    def test_previously_generated_instructs_different_activity(self):
        builder = PromptBuilder()
        previously = [
            {"title": "X", "country": "US",
             "availabilityType": "START_TIME", "categoryLabels": []},
        ]
        prompt = builder.build_prompt(previously_generated=previously)
        assert "DIFFERENT type of activity" in prompt
        assert "DIFFERENT country" in prompt
        assert "MUST NOT reuse the same title" in prompt

    def test_both_error_hints_and_previously_generated(self):
        builder = PromptBuilder()
        prompt = builder.build_prompt(
            error_hints=["some error"],
            previously_generated=[
                {"title": "Tour", "country": "FR",
                 "availabilityType": "START_TIME", "categoryLabels": []},
            ],
        )
        assert "### Diversity" in prompt
        assert "Previous Attempt Errors" in prompt
        assert "some error" in prompt
        assert "Tour" in prompt
