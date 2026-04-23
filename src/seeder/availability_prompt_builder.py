"""Prompt construction for OCTO Availability Calendar generation via Ollama.

Builds a prompt that includes:
- The availability calendar JSON schema (from octo-std/)
- Product context (so the LLM generates coherent availability)
- Dynamic error hints from previous failed attempts
"""

import logging
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_OCTO_STD_DIR = Path(__file__).resolve().parent.parent.parent / "octo-std"


def _load_spec_schema(spec_file: str = "post-availability-calendar.yaml") -> str:
    """Load the availability calendar spec and format it for the LLM."""
    path = _OCTO_STD_DIR / spec_file
    if not path.exists():
        logger.warning("Spec file not found: %s", path)
        return "(schema file not found)"

    with open(path, "r", encoding="utf-8") as f:
        spec = yaml.safe_load(f)

    endpoint = spec.get("endpoint", "unknown")
    summary = spec.get("summary", "")
    schema_yaml = yaml.dump(
        spec.get("response", {}), default_flow_style=False, sort_keys=False
    )

    return (
        f"The following is the OpenAPI response schema for {endpoint} ({summary}).\n"
        f"Use it as the authoritative reference for field names, types, and enums.\n\n"
        f"```yaml\n{schema_yaml}```"
    )


def _build_product_context(product_data: dict, option_id: str) -> str:
    """Build a concise product summary for the LLM to reason about.

    Includes duration and scheduling fields that constrain availability.
    """
    title = product_data.get("title") or product_data.get("internalName", "Unknown")
    country = product_data.get("country", "Unknown")
    tz = product_data.get("timeZone", "Unknown")
    avail_type = product_data.get("availabilityType", "Unknown")
    categories = product_data.get("categoryLabels", [])
    short_desc = product_data.get("shortDescription", "")
    allow_freesale = product_data.get("allowFreesale", False)
    dur_from = product_data.get("durationMinutesFrom")
    dur_to = product_data.get("durationMinutesTo")

    lines = [
        f"Product: {title}",
        f"Country: {country}",
        f"Timezone: {tz}",
        f"AvailabilityType: {avail_type}",
        f"Categories: {categories}",
        f"Description: {short_desc}",
        f"AllowFreesale: {allow_freesale}",
    ]
    if dur_from is not None or dur_to is not None:
        lines.append(f"Product duration range: {dur_from}–{dur_to} minutes")

    # Detailed info for the target option
    for opt in product_data.get("options", []):
        opt_name = opt.get("title") or opt.get("internalName", "Unknown")
        start_times = opt.get("availabilityLocalStartTimes", [])
        units = [u.get("internalName", "?") for u in opt.get("units", [])]
        dur_amount = opt.get("durationAmount")
        dur_unit = opt.get("durationUnit")

        prefix = ">>> " if opt.get("id") == option_id else "    "
        line = f"{prefix}Option '{opt_name}' (id: {opt.get('id', '?')}): startTimes={start_times}, units={units}"
        if dur_amount and dur_unit:
            line += f", duration={dur_amount} {dur_unit}"
        lines.append(line)

    return "\n".join(lines)


class AvailabilityPromptBuilder:
    """Builds LLM prompts for generating availability calendar data."""

    def build_prompt(
        self,
        product_data: dict,
        option_id: str,
        num_days: int,
        start_date: str,
        max_slots: int = 5,
        error_hints: list[str] | None = None,
    ) -> str:
        """Build the full prompt for generating availability calendar days.

        Args:
            product_data: Full product dict (from seed dump).
            option_id: The option ID to generate availability for.
            num_days: Number of days to generate (max 7, one week chunk).
            start_date: Start date (YYYY-MM-DD).
            max_slots: Max available slots in this week.
            error_hints: Validation error messages from previous attempts.
        """
        parts: list[str] = []

        # System instruction
        parts.append(
            "You are a data generator for the OCTO Travel API standard. "
            "Your task is to generate a valid JSON array of availability "
            "calendar day objects for a specific product and option."
        )

        # Schema
        parts.append("\n## Availability Calendar Response Schema\n")
        parts.append(_load_spec_schema("post-availability-calendar.yaml"))

        # Product context
        parts.append("\n## Product Context\n")
        parts.append(
            "Generate availability that is COHERENT with this product. "
            "Consider the product type, country, timezone, season, "
            "duration, and defined start times.\n"
        )
        parts.append(_build_product_context(product_data, option_id))

        # Option-specific scheduling constraints
        option_data = None
        for opt in product_data.get("options", []):
            if opt.get("id") == option_id:
                option_data = opt
                break

        if option_data:
            avail_type = product_data.get("availabilityType", "OPENING_HOURS")
            start_times = option_data.get("availabilityLocalStartTimes", [])
            dur_amount = option_data.get("durationAmount")
            dur_unit = option_data.get("durationUnit")
            allow_freesale = product_data.get("allowFreesale", False)

            parts.append("\n### Target Option Constraints")
            parts.append(f"Option ID: {option_id}")
            parts.append(f"Option name: {option_data.get('title') or option_data.get('internalName')}")

            if avail_type == "START_TIME" and start_times:
                parts.append(
                    f"\nThis is a START_TIME product. The option defines these "
                    f"start times: {start_times}."
                )
                parts.append(
                    "CRITICAL: On open days, availabilityLocalStartTimes MUST be "
                    "a SUBSET of the above list. Do NOT invent new times. "
                    "You may use all of them or only some (to simulate partial "
                    "availability or sold-out slots). Not every day needs all times. "
                    "Vary the subset across days — some days might offer all "
                    "slots, others just the morning, others just the afternoon. "
                    "Think of it as real demand: certain time slots sell out on "
                    "busy days."
                )
                if dur_amount and dur_unit:
                    parts.append(
                        f"\nThe option duration is {dur_amount} {dur_unit}. "
                        "Start times must NOT overlap when you account for this "
                        "duration. For example, if duration is 2 hours, you cannot "
                        "have both 09:00 and 10:00 — the 09:00 session ends at 11:00."
                    )
                parts.append(
                    "\nFor CLOSED or SOLD_OUT days, do NOT include "
                    "availabilityLocalStartTimes."
                )
            else:
                parts.append(
                    "\nThis is an OPENING_HOURS product. "
                    "Do NOT include availabilityLocalStartTimes. "
                    "Include realistic openingHours entries instead."
                )

            if not allow_freesale:
                parts.append(
                    "\nThis product does NOT allow freesale. "
                    "Do NOT use FREESALE status."
                )

        # Generation instructions
        parts.append("\n## Generation Instructions\n")
        parts.append(self._build_generation_instructions(
            num_days=num_days,
            start_date=start_date,
            max_slots=max_slots,
        ))

        # Error hints
        if error_hints:
            parts.append("\n## Previous Attempt Errors — MUST FIX\n")
            parts.append(
                "Your previous attempts were rejected due to the following "
                "validation errors. You MUST avoid these mistakes:\n"
            )
            for i, hint in enumerate(error_hints, 1):
                parts.append(f"{i}. {hint}")

        return "\n".join(parts)

    def _build_generation_instructions(
        self,
        num_days: int,
        start_date: str,
        max_slots: int,
    ) -> str:
        """Build detailed generation instructions for availability calendar."""
        return (
            f"Generate a JSON array of approximately {num_days} availability "
            f"calendar day objects, one per day, starting from {start_date}. "
            f"Do NOT generate more than {int(num_days * 1.25)} days.\n\n"
            "### Date Sequence\n"
            f"1. Generate consecutive dates starting from {start_date}. "
            f"Each object's localDate must be the next day in sequence.\n\n"
            "### Slot Frequency\n"
            f"2. A 'slot' is one bookable time entry. For START_TIME products "
            "each entry in availabilityLocalStartTimes counts as one slot. "
            "For OPENING_HOURS products each open day counts as one slot.\n"
            f"3. The total number of slots across all open days in this "
            f"{num_days}-day chunk MUST NOT exceed {max_slots}. "
            "For example, if max is 10 and you have 3 open days, you could "
            "give them 4, 3, and 3 start times respectively. "
            "Close the remaining days (CLOSED or SOLD_OUT).\n\n"
            "### Status Rules\n"
            "4. Among the open days, use a realistic mix:\n"
            "   - Mostly AVAILABLE\n"
            "   - Occasionally FREESALE or LIMITED\n"
            "5. When status is AVAILABLE or LIMITED: available=true, "
            "vacancies=integer (positive), capacity=integer.\n"
            "6. When status is FREESALE: available=true, vacancies=null, "
            "capacity=null.\n"
            "7. When status is SOLD_OUT: available=false, vacancies=0, "
            "capacity=integer.\n"
            "8. When status is CLOSED: available=false, vacancies=null, "
            "capacity=null.\n\n"
            "### Capacity Logic\n"
            "9. capacity should be a realistic number for the product type "
            "(e.g. 20-50 for tours, 100-500 for attractions, 10-30 for boats).\n"
            "10. vacancies must be <= capacity when both are present.\n"
            "11. For LIMITED status, vacancies should be < 50% of capacity.\n"
            "12. Vary vacancies across days — do NOT use the same "
            "number for every day. Simulate real booking patterns where some "
            "days are busier than others. For example, weekends might have "
            "fewer vacancies than weekdays.\n\n"
            "### Opening Hours\n"
            "13. For OPENING_HOURS products: include 1-2 openingHours entries "
            "with realistic from/to times (e.g. '09:00' to '17:00').\n"
            "14. For START_TIME products: openingHours MUST be an empty array [].\n\n"
            "### Extended Fields\n"
            "15. Optionally include statusMessage for some days "
            "(e.g. 'Available', 'Sold Out', 'Closed for maintenance').\n"
            "16. Do NOT include weight-related fields (availableWeight, "
            "maxWeight, weightUnit, paxWeight, totalPaxWeight, totalMaxWeight) "
            "unless the product is clearly weight-based (e.g. skydiving, "
            "bungee jumping, tandem paragliding). For all other products "
            "set these to null or omit them.\n"
            "17. maxUnits: include occasionally, realistic values (4-10).\n\n"
            "### Realism\n"
            "17. Think like a real supplier managing a booking system. "
            "Consider the product's country, timezone, and season. "
            "Vacancies should fluctuate naturally — some days are busier "
            "than others, weekends differ from weekdays, and popular "
            "products sell out faster. Capacity stays consistent for a "
            "given product but vacancies should tell a story of real "
            "booking activity.\n"
            "18. Only include extended fields (weight, pax, etc.) when "
            "they make sense for the product type. A walking tour doesn't "
            "track passenger weight; a skydiving experience might.\n"
            "19. Do NOT generate identical days — each day should feel "
            "like a snapshot of a live booking system.\n\n"
            "### Output Format\n"
            "20. Return ONLY the raw JSON array. No markdown, no explanation, "
            "no wrapping.\n"
        )
