"""Prompt construction for OCTO Product generation via Ollama.

Builds a system prompt that includes the OCTO Product JSON schema
(loaded from the ``octo-std/`` spec files) and explicit instructions
to generate synthetic data with realistic patterns matching
typical OCTO supplier responses.
"""

import logging
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


# Default location of the split spec files
_OCTO_STD_DIR = Path(__file__).resolve().parent.parent.parent / "octo-std"


def _load_spec_schema(spec_file: str = "get-products.yaml") -> str:
    """Load an OCTO endpoint spec from ``octo-std/`` and format it for the LLM.

    Returns a YAML-formatted schema description string that replaces the
    previously hardcoded ``_SCHEMA_DESCRIPTION``.
    """
    path = _OCTO_STD_DIR / spec_file
    if not path.exists():
        logger.warning("Spec file not found: %s — falling back to empty schema", path)
        return "(schema file not found)"

    with open(path, "r", encoding="utf-8") as f:
        spec = yaml.safe_load(f)

    # Build a concise text representation the LLM can reason about
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


class PromptBuilder:
    """Builds LLM prompts for generating synthetic OCTO Product JSON."""

    def __init__(
        self,
        avg_slots_per_day: int = 3,
    ) -> None:
        self._avg_slots_per_day = avg_slots_per_day

    def build_prompt(
        self,
        error_hints: list[str] | None = None,
        previously_generated: list[dict] | None = None,
    ) -> str:
        """Build the full prompt for generating one OCTO Product.

        Args:
            error_hints: Optional list of validation error messages from
                previous failed attempts. These are injected as explicit
                constraints so the LLM avoids repeating the same mistakes.
            previously_generated: Optional list of compact product summaries
                (dicts with keys: title, country, availabilityType,
                categoryLabels) from products already generated in this batch.
                Used to steer the LLM toward diverse output.
        """
        parts: list[str] = []

        # System instruction
        parts.append(
            "You are a data generator for the OCTO Travel API standard. "
            "Your task is to generate exactly ONE valid OCTO Product JSON object "
            "that looks indistinguishable from a typical supplier's response."
        )

        # Schema — loaded from octo-std/ spec files
        parts.append("\n## OCTO Product JSON Schema\n")
        parts.append(_load_spec_schema("get-products.yaml"))

        # Generation instructions — rewritten per P1 analysis findings
        parts.append("\n## Generation Instructions\n")
        parts.append(self._build_generation_instructions(previously_generated))

        # Error hints from previous failed attempts
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
        previously_generated: list[dict] | None = None,
    ) -> str:
        """Build detailed generation instructions for realistic OCTO products."""
        text = (
            "Generate exactly ONE fictional OCTO Product JSON object following "
            "these rules:\n\n"
            "### IDs and References\n"
            "1. Generate fresh UUID v4 values for the product id, each option "
            "id, and each unit id.\n"
            "2. Set `reference` to null for all products, options, and units. "
            "Suppliers use null or short codes — never UUIDs.\n\n"
            "### Product Structure\n"
            "3. The product MUST have at least one Option, and each Option "
            "MUST have at least one Unit.\n"
            "4. Generate 1 to 5 options per product (average ~3). Multiple "
            "options represent tiers, departure points, or packages.\n\n"
            "### Product Names\n"
            "5. Use descriptive, production-style product names that describe "
            "the actual activity. Good: 'General Admission', 'Sunset Cruise', "
            "'Tour & Tasting Standard', 'Explorer Pass 4-Choice'. "
            "Bad: 'Enchanted Forest Adventure', 'Magical Journey Tour'.\n\n"
            "### Locale\n"
            "6. Use 'en' for locale (~85% of products). "
            "Only occasionally use 'en-US' or other regional tags.\n\n"
            "### Availability Type\n"
            "7. Choose availabilityType randomly: ~55% START_TIME, ~45% "
            "OPENING_HOURS.\n"
            "8. For START_TIME products, include between 1 and 20 entries in "
            "availabilityLocalStartTimes (vary the count — some have 1-3, "
            "venues may have 10-27 at 30-min intervals). "
            "For OPENING_HOURS products, availabilityLocalStartTimes MUST be "
            "an empty array [].\n\n"
            "### Cancellation Cutoff\n"
            "9. cancellationCutoff MUST match the pattern '{amount} {unit}(s)'. "
            "Examples: '0 hours', '24 hours', '48 hours', '7 days', '365 days'. "
            "NEVER use bare unit names like 'hour'.\n\n"
            "### Options\n"
            "10. Set `default` to false for most options. Only sometimes set "
            "one option to true. ~70% of products have ALL options as "
            "default=false.\n\n"
            "### Unit Types and Names\n"
            "11. Use ADULT and CHILD as the common base. Add YOUTH, STUDENT, "
            "FAMILY, INFANT as needed. Use SENIOR less frequently.\n"
            "12. Unit internalName MUST be bare names: 'Adult', 'Child', "
            "'Student', 'Infant', 'Youth'. Do NOT add 'Ticket' suffix.\n\n"
            "### Age-Appropriateness and Safety\n"
            "CRITICAL: Every unit's features, descriptions, and inclusions "
            "MUST be appropriate for the age group of that unit. Never include "
            "alcohol-related experiences (wine tasting, beer tours, cocktail "
            "making, etc.) for CHILD, YOUTH, or INFANT units. Never include "
            "activities that are unsafe, legally restricted, or culturally "
            "inappropriate for minors — examples: gambling, nightlife, extreme "
            "sports with age restrictions, tobacco or cannabis experiences. "
            "If the product itself is inherently adult-only (e.g. a wine tour "
            "or pub crawl), either omit child/youth units entirely or ensure "
            "their description explicitly offers an age-appropriate alternative "
            "(e.g. 'juice tasting', 'soft drinks included'). Apply common sense: "
            "if a reasonable parent would object, fix it.\n\n"
            "### Contact Fields\n"
            "13. requiredContactFields for units: almost always an empty "
            "array []. Do NOT add firstName/lastName to units.\n"
            "14. requiredContactFields for options: use [] or at most "
            "['emailAddress']. Do NOT over-specify.\n\n"
            "### Restrictions\n"
            "15. maxQuantity on units: use null frequently (means unlimited). "
            "Only sometimes use a specific number.\n"
            "16. maxUnits on options: use null frequently (means unlimited).\n"
            "17. Adult maxAge: use 0 (no limit) most often, or realistic "
            "values like 99, 100, 65. Never use 120.\n\n"
            "### Delivery Formats\n"
            "18. Vary deliveryFormats. Common combos: "
            "['QRCODE', 'PNG_URL', 'PKPASS_URL', 'GOOGLE_WALLET_URL', 'PDF_URL'], "
            "or ['HTML_URL', 'PDF_URL'], or just ['QRCODE'].\n\n"
            "### OCTO Pricing Capability (octo/pricing)\n"
            "19. Include pricing fields on the product: defaultCurrency (ISO 4217, "
            "e.g. 'USD', 'EUR', 'GBP'), availableCurrencies (usually just "
            "[defaultCurrency]), pricingPer ('UNIT'), includeTax (boolean).\n"
            "20. Include pricingFrom on each unit: an array with one object per "
            "currency. Amounts are integers in cents (e.g. 3500 = $35.00). "
            "net <= retail <= original. net can be null. currencyPrecision is "
            "typically 2. includedTaxes can be an empty array.\n\n"
            "### OCTO Content Capability (octo/content)\n"
            "21. Product level: include title, shortDescription (1-2 sentences), "
            "country (ISO 3166-1 alpha-2). ALWAYS include description — it MUST "
            "be a rich, detailed text of approximately 250 words that reads like "
            "a real supplier's marketing copy. Cover what the experience includes, "
            "what guests will see or do, practical details (meeting point, what "
            "to bring, accessibility notes), and why this activity stands out. "
            "Use Markdown for formatting (e.g. bullet lists with '- ', bold with "
            "'**text**'). Do NOT use HTML tags or HTML entities anywhere in the "
            "description or in the shortDescription. "
            "Also include features (array of {shortDescription, type}), faqs, "
            "media, locations, categoryLabels, durationMinutesFrom/To.\n"
            "22. Option level: include title. Include shortDescription (1-2 "
            "sentences) and a description of approximately 100 words covering "
            "what this specific option offers and how it differs from other "
            "options. Use Markdown for formatting, no HTML tags or entities. "
            "Optionally include duration, durationAmount, durationUnit.\n"
            "23. Unit level: include title (e.g. 'Adult'), titlePlural "
            "(e.g. 'Adults'), subtitle (e.g. '18 - 65 Years'). Include a "
            "shortDescription (1-2 sentences) explaining what this ticket "
            "type covers.\n\n"
            "### OCTO Pickups Capability (octo/pickups)\n"
            "24. For some products (especially tours with transport), include "
            "pickupAvailable, pickupRequired on options. If pickupAvailable is "
            "true, include pickupLocations array with {id (UUID), title, "
            "shortDescription, place{latitude, longitude, postalAddress}}.\n"
            "25. Most products do NOT have pickup. Only include it for ~20% "
            "of products.\n\n"
            "### Output Format\n"
            "26. Return ONLY the raw JSON object. No markdown, no explanation, "
            "no wrapping.\n"
        )

        if previously_generated:
            lines = [
                "\n### Diversity\n"
                "27. The following products have already been generated in this "
                "batch. You MUST generate a product that is a DIFFERENT type of "
                "activity, in a DIFFERENT country if possible, and MUST NOT "
                "reuse the same title. Vary the availabilityType, categoryLabels, "
                "and pricing.\n"
            ]
            for i, summary in enumerate(previously_generated, 1):
                lines.append(f"  {i}. {summary}")
            text += "\n".join(lines) + "\n"

        return text
