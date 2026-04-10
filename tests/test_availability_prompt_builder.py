"""Tests for AvailabilityPromptBuilder — prompt construction and content."""

from seeder.availability_prompt_builder import AvailabilityPromptBuilder


def _product(avail_type="START_TIME", allow_freesale=False):
    return {
        "id": "prod-1",
        "title": "Sunset Cruise",
        "internalName": "Sunset Cruise",
        "country": "US",
        "timeZone": "America/New_York",
        "availabilityType": avail_type,
        "allowFreesale": allow_freesale,
        "categoryLabels": ["Boat Tours"],
        "shortDescription": "A scenic sunset cruise.",
        "options": [{
            "id": "opt-1",
            "title": "Standard",
            "internalName": "Standard",
            "availabilityLocalStartTimes": ["18:00", "19:30"],
            "durationAmount": "2",
            "durationUnit": "hour",
            "units": [{"internalName": "Adult"}],
        }],
    }


class TestPromptContainsSchema:
    def test_includes_schema(self):
        builder = AvailabilityPromptBuilder()
        prompt = builder.build_prompt(
            product_data=_product(), option_id="opt-1",
            num_days=7, start_date="2026-04-10",
        )
        assert "Availability Calendar" in prompt
        assert "localDate" in prompt

    def test_includes_status_enum(self):
        builder = AvailabilityPromptBuilder()
        prompt = builder.build_prompt(
            product_data=_product(), option_id="opt-1",
            num_days=7, start_date="2026-04-10",
        )
        assert "AVAILABLE" in prompt
        assert "SOLD_OUT" in prompt
        assert "CLOSED" in prompt


class TestPromptContainsProductContext:
    def test_includes_product_title(self):
        builder = AvailabilityPromptBuilder()
        prompt = builder.build_prompt(
            product_data=_product(), option_id="opt-1",
            num_days=7, start_date="2026-04-10",
        )
        assert "Sunset Cruise" in prompt

    def test_includes_country_and_timezone(self):
        builder = AvailabilityPromptBuilder()
        prompt = builder.build_prompt(
            product_data=_product(), option_id="opt-1",
            num_days=7, start_date="2026-04-10",
        )
        assert "US" in prompt
        assert "America/New_York" in prompt

    def test_includes_start_times(self):
        builder = AvailabilityPromptBuilder()
        prompt = builder.build_prompt(
            product_data=_product(), option_id="opt-1",
            num_days=7, start_date="2026-04-10",
        )
        assert "18:00" in prompt
        assert "19:30" in prompt

    def test_includes_duration(self):
        builder = AvailabilityPromptBuilder()
        prompt = builder.build_prompt(
            product_data=_product(), option_id="opt-1",
            num_days=7, start_date="2026-04-10",
        )
        assert "2 hour" in prompt

    def test_subset_instruction_for_start_time(self):
        builder = AvailabilityPromptBuilder()
        prompt = builder.build_prompt(
            product_data=_product(), option_id="opt-1",
            num_days=7, start_date="2026-04-10",
        )
        assert "SUBSET" in prompt

    def test_overlap_instruction_with_duration(self):
        builder = AvailabilityPromptBuilder()
        prompt = builder.build_prompt(
            product_data=_product(), option_id="opt-1",
            num_days=7, start_date="2026-04-10",
        )
        assert "overlap" in prompt.lower()


class TestPromptOpeningHoursProduct:
    def test_no_start_times_instruction(self):
        builder = AvailabilityPromptBuilder()
        prompt = builder.build_prompt(
            product_data=_product(avail_type="OPENING_HOURS"),
            option_id="opt-1", num_days=7, start_date="2026-04-10",
        )
        assert "Do NOT include availabilityLocalStartTimes" in prompt

    def test_includes_opening_hours_instruction(self):
        builder = AvailabilityPromptBuilder()
        prompt = builder.build_prompt(
            product_data=_product(avail_type="OPENING_HOURS"),
            option_id="opt-1", num_days=7, start_date="2026-04-10",
        )
        assert "openingHours" in prompt


class TestPromptFreesaleControl:
    def test_no_freesale_when_not_allowed(self):
        builder = AvailabilityPromptBuilder()
        prompt = builder.build_prompt(
            product_data=_product(allow_freesale=False),
            option_id="opt-1", num_days=7, start_date="2026-04-10",
        )
        assert "Do NOT use FREESALE" in prompt

    def test_no_restriction_when_allowed(self):
        builder = AvailabilityPromptBuilder()
        prompt = builder.build_prompt(
            product_data=_product(allow_freesale=True),
            option_id="opt-1", num_days=7, start_date="2026-04-10",
        )
        assert "Do NOT use FREESALE" not in prompt


class TestPromptSlotBudget:
    def test_includes_max_slots(self):
        builder = AvailabilityPromptBuilder()
        prompt = builder.build_prompt(
            product_data=_product(), option_id="opt-1",
            num_days=7, start_date="2026-04-10", max_slots=10,
        )
        assert "10" in prompt
        assert "MUST NOT exceed" in prompt


class TestPromptErrorHints:
    def test_includes_error_hints(self):
        builder = AvailabilityPromptBuilder()
        prompt = builder.build_prompt(
            product_data=_product(), option_id="opt-1",
            num_days=7, start_date="2026-04-10",
            error_hints=["vacancies must be null for FREESALE"],
        )
        assert "Previous Attempt Errors" in prompt
        assert "vacancies must be null for FREESALE" in prompt

    def test_no_error_section_without_hints(self):
        builder = AvailabilityPromptBuilder()
        prompt = builder.build_prompt(
            product_data=_product(), option_id="opt-1",
            num_days=7, start_date="2026-04-10",
        )
        assert "Previous Attempt Errors" not in prompt


class TestPromptDateAndDays:
    def test_includes_start_date(self):
        builder = AvailabilityPromptBuilder()
        prompt = builder.build_prompt(
            product_data=_product(), option_id="opt-1",
            num_days=7, start_date="2026-05-01",
        )
        assert "2026-05-01" in prompt

    def test_includes_num_days(self):
        builder = AvailabilityPromptBuilder()
        prompt = builder.build_prompt(
            product_data=_product(), option_id="opt-1",
            num_days=5, start_date="2026-04-10",
        )
        assert "5" in prompt
