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
        assert "approximately 5 entries" in prompt

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
